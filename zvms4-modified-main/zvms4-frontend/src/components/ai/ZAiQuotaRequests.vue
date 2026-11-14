<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { resolveErrorMessageOr } from '@/utils/errorMessage'
import {
  approveQuotaRequest,
  listMyQuotaRequests,
  listQuotaRequests,
  rejectQuotaRequest,
  submitQuotaRequest
} from '@/api/next/ai/quotaRequests'
import type {
  QuotaRequestKind,
  QuotaRequestRow,
  QuotaRequestStatus
} from '@/api/next/ai/quotaRequests'

/**
 * AI 额度申请与审批（/ai/quota/requests）。
 *
 * 上半：任何人都能提交、并看到自己最近 10 条；
 * 下半：admin（或具备 quota.manage 的角色）看到审批队列，可批准/驳回。
 */

const userStore = useUserStore()
const isAdmin = computed(() => userStore.position.includes('admin'))

const mine = ref<QuotaRequestRow[]>([])
const mineLoading = ref(false)
const mineError = ref('')

const kind = ref<QuotaRequestKind>('chat')
const reason = ref('')
const requestedLimit = ref<number>()
const submitting = ref(false)

const queue = ref<QuotaRequestRow[]>([])
const queueLoading = ref(false)
const queueError = ref('')
const queueStatus = ref<QuotaRequestStatus | 'all'>('pending')
const approverDenied = ref(false)
const deciding = ref('')

const statusText: Record<QuotaRequestStatus, string> = {
  pending: '待审批',
  approved: '已通过',
  rejected: '已驳回',
  cancelled: '已撤销'
}

const kindText: Record<QuotaRequestKind, string> = {
  chat: '对话额度',
  ocr: 'OCR 额度'
}

function fmtTime(value: string | null): string {
  if (!value) {
    return '—'
  }
  return value.replace('T', ' ').slice(0, 16)
}

async function loadMine() {
  mineLoading.value = true
  mineError.value = ''
  try {
    mine.value = await listMyQuotaRequests()
  } catch (error) {
    mineError.value = resolveErrorMessageOr(error, '申请记录加载失败')
  } finally {
    mineLoading.value = false
  }
}

async function loadQueue() {
  if (!isAdmin.value) {
    return
  }
  queueLoading.value = true
  queueError.value = ''
  try {
    const page = await listQuotaRequests({ status: queueStatus.value })
    queue.value = page.items
    approverDenied.value = false
  } catch (error) {
    if ((error as { status?: number })?.status === 403) {
      approverDenied.value = true
      queue.value = []
    } else {
      queueError.value = resolveErrorMessageOr(error, '审批队列加载失败')
    }
  } finally {
    queueLoading.value = false
  }
}

async function submit() {
  if (submitting.value) {
    return
  }
  submitting.value = true
  try {
    await submitQuotaRequest(
      kind.value,
      reason.value.trim() || undefined,
      typeof requestedLimit.value === 'number' ? requestedLimit.value : undefined
    )
    ElMessage.success('已提交，等待审批')
    reason.value = ''
    requestedLimit.value = undefined
    await loadMine()
    void loadQueue()
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '提交失败'))
  } finally {
    submitting.value = false
  }
}

async function approve(row: QuotaRequestRow) {
  let limit: number | undefined
  try {
    const input = await ElMessageBox.prompt(
      `通过后会把「${row.user_name || row.user_id || '该用户'}」的已用量清零。要同时改上限就填数字，留空则只清零。`,
      '通过申请',
      {
        confirmButtonText: '通过',
        cancelButtonText: '取消',
        inputPlaceholder: '新上限（可留空）',
        inputPattern: /^\d*$/,
        inputErrorMessage: '只能填数字'
      }
    )
    const raw = (input as { value?: string }).value?.trim()
    limit = raw ? Number(raw) : undefined
  } catch {
    return
  }
  deciding.value = row.oid
  try {
    await approveQuotaRequest(row.oid, { reset: true, limit })
    ElMessage.success('已通过')
    await loadQueue()
    void loadMine()
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '审批失败'))
  } finally {
    deciding.value = ''
  }
}

async function reject(row: QuotaRequestRow) {
  let note: string | undefined
  try {
    const input = await ElMessageBox.prompt('驳回理由会记录在申请上，申请人能看到。', '驳回申请', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：本月额度已用尽且无特殊用途',
      inputType: 'textarea'
    })
    note = (input as { value?: string }).value?.trim() || undefined
  } catch {
    return
  }
  deciding.value = row.oid
  try {
    await rejectQuotaRequest(row.oid, note)
    ElMessage.success('已驳回')
    await loadQueue()
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '驳回失败'))
  } finally {
    deciding.value = ''
  }
}

onMounted(() => {
  void loadMine()
  void loadQueue()
})
</script>

<template>
  <div class="qr">
    <section class="yg2-card qr-card">
      <header class="qr-head">
        <h2 class="yg2-card__title">我的额度申请</h2>
        <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" @click="loadMine">
          刷新
        </button>
      </header>

      <form class="qr-form" @submit.prevent="submit">
        <label class="yg2-field qr-field">
          <span class="yg2-field__label">申请类别</span>
          <select v-model="kind" class="yg2-input">
            <option value="chat">{{ kindText.chat }}</option>
            <option value="ocr">{{ kindText.ocr }}</option>
          </select>
        </label>
        <label class="yg2-field qr-field">
          <span class="yg2-field__label">希望上限（可留空）</span>
          <input
            v-model.number="requestedLimit"
            class="yg2-input"
            type="number"
            min="0"
            placeholder="留空表示只申请清零"
          />
        </label>
        <label class="yg2-field qr-field qr-field--wide">
          <span class="yg2-field__label">用途说明</span>
          <input
            v-model="reason"
            class="yg2-input"
            type="text"
            maxlength="200"
            placeholder="例如：本周批量登记要用 OCR 识别"
          />
        </label>
        <button
          class="yg2-btn yg2-btn--primary"
          type="submit"
          :disabled="submitting"
        >
          {{ submitting ? '提交中…' : '提交申请' }}
        </button>
      </form>

      <p v-if="mineError" class="qr-hint qr-hint--error">{{ mineError }}</p>
      <p v-else-if="mineLoading" class="qr-hint">加载中…</p>
      <p v-else-if="!mine.length" class="qr-hint">还没有提交过申请。</p>

      <ul v-else class="qr-list">
        <li v-for="row in mine" :key="row.oid" class="qr-item">
          <span class="qr-badge" :class="`qr-badge--${row.status}`">
            {{ statusText[row.status] }}
          </span>
          <span class="qr-item__main">
            {{ kindText[row.kind] || row.kind }}
            <span v-if="row.requested_limit"> · 申请上限 {{ row.requested_limit }}</span>
            <span v-if="row.reason" class="qr-item__reason"> · {{ row.reason }}</span>
          </span>
          <span class="qr-item__time">{{ fmtTime(row.created_at) }}</span>
        </li>
      </ul>
    </section>

    <section v-if="isAdmin" class="yg2-card qr-card">
      <header class="qr-head">
        <h2 class="yg2-card__title">审批队列</h2>
        <div class="qr-filters">
          <select v-model="queueStatus" class="yg2-input qr-select" @change="loadQueue">
            <option value="pending">待审批</option>
            <option value="approved">已通过</option>
            <option value="rejected">已驳回</option>
            <option value="all">全部</option>
          </select>
          <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" @click="loadQueue">
            刷新
          </button>
        </div>
      </header>

      <p v-if="approverDenied" class="qr-hint">
        你的账号没有审批权（需要 admin 或 quota.manage 角色）。
      </p>
      <p v-else-if="queueError" class="qr-hint qr-hint--error">{{ queueError }}</p>
      <p v-else-if="queueLoading" class="qr-hint">加载中…</p>
      <p v-else-if="!queue.length" class="qr-hint">队列是空的。</p>

      <ul v-else class="qr-list">
        <li v-for="row in queue" :key="row.oid" class="qr-item qr-item--queue">
          <span class="qr-badge" :class="`qr-badge--${row.status}`">
            {{ statusText[row.status] }}
          </span>
          <span class="qr-item__main">
            <strong>{{ row.user_name || row.user_id || row.user_oid }}</strong>
            · {{ kindText[row.kind] || row.kind }}
            <span v-if="row.requested_limit"> · 申请上限 {{ row.requested_limit }}</span>
            <span v-if="row.reason" class="qr-item__reason"> · {{ row.reason }}</span>
          </span>
          <span class="qr-item__time">{{ fmtTime(row.created_at) }}</span>
          <span v-if="row.status === 'pending'" class="qr-actions">
            <button
              class="yg2-btn yg2-btn--sm yg2-btn--primary"
              type="button"
              :disabled="deciding === row.oid"
              @click="approve(row)"
            >
              通过
            </button>
            <button
              class="yg2-btn yg2-btn--sm yg2-btn--danger"
              type="button"
              :disabled="deciding === row.oid"
              @click="reject(row)"
            >
              驳回
            </button>
          </span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.qr {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.qr-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
}

.qr-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.qr-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
}

.qr-field {
  flex: 0 0 180px;
}

.qr-field--wide {
  flex: 1 1 240px;
}

.qr-filters {
  display: flex;
  align-items: center;
  gap: 8px;
}

.qr-select {
  height: 32px;
  width: 132px;
}

.qr-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.qr-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--yg2-r-10, 10px);
  background: var(--yg2-surface-sunken);
  font-size: 13px;
  color: var(--yg2-text);
}

.qr-item--queue {
  flex-wrap: wrap;
}

.qr-item__main {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qr-item__reason {
  color: var(--yg2-text-secondary);
}

.qr-item__time {
  flex: none;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.qr-actions {
  display: flex;
  gap: 6px;
}

.qr-badge {
  flex: none;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
}

.qr-badge--pending {
  background: var(--yg2-amber-soft);
  color: var(--yg2-amber);
}

.qr-badge--approved {
  background: color-mix(in srgb, var(--yg2-cat-community) 16%, transparent);
  color: var(--yg2-cat-community);
}

.qr-badge--rejected {
  background: color-mix(in srgb, var(--yg2-cat-school) 16%, transparent);
  color: var(--yg2-cat-school);
}

.qr-badge--cancelled {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-tertiary);
}

.qr-hint {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-tertiary);
}

.qr-hint--error {
  color: var(--yg2-act-danger-text, #b23a34);
}
</style>

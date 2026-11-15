<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  decideAttachment,
  getAttachmentAudit,
  listAuditAttachments,
  type AttachmentDecision
} from '@/api/next/attachment/audit'
import type {
  AttachmentAuditDetail,
  AttachmentAuditListItem
} from '@/api/next/types-compat'
import { errorMessage } from '@/utils/errorMessage'

/**
 * 附件审核：分页列表 + 筛选 + 详情（OCR 正文与事件流）+ 通过/驳回。
 *
 * 裁决是留痕动作，失败必须显式提示；需要二次验证时由后端返回原因，
 * 这里原样展示，不做二次猜测。
 */
const loading = ref(true)
const rows = ref<AttachmentAuditListItem[]>([])

/** 后端列表同时存在 `_id/original_name/size_bytes` 与 `oid/name/size` 两套命名。 */
interface RawAttachmentRow extends Partial<AttachmentAuditListItem> {
  _id?: string
  original_name?: string
  size_bytes?: number
  owner_oid?: string
}

function normalizeRow(raw: RawAttachmentRow): AttachmentAuditListItem {
  return {
    ...(raw as AttachmentAuditListItem),
    oid: raw.oid || raw._id || '',
    name: raw.name || raw.original_name || '(未命名)',
    size: raw.size ?? raw.size_bytes ?? 0,
    uploader_oid: raw.uploader_oid || raw.owner_oid || '',
    uploader_name: raw.uploader_name || ''
  }
}
const total = ref(0)
const page = ref(1)
const size = ref(20)
const status = ref('')
const purpose = ref('')
const error = ref('')

const detailOpen = ref(false)
const detail = ref<AttachmentAuditDetail | null>(null)
const detailLoading = ref(false)
const deciding = ref(false)

const pages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

const STATUS_LABEL: Record<string, string> = {
  pending: '待审核',
  verified: '已通过',
  rejected: '已驳回',
  quarantined: '已隔离',
  deleted: '已删除'
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listAuditAttachments({
      page: page.value,
      size: size.value,
      status: status.value || undefined,
      purpose: purpose.value || undefined
    })
    rows.value = data.data.map(normalizeRow)
    total.value = data.total
  } catch (e) {
    error.value = errorMessage(e, '读取附件列表失败')
  } finally {
    loading.value = false
  }
}

async function openDetail(row: AttachmentAuditListItem) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const raw = (await getAttachmentAudit(row.oid)) as RawAttachmentRow
    detail.value = normalizeRow(raw) as AttachmentAuditDetail
  } catch (e) {
    ElMessage.error(errorMessage(e, '读取审计详情失败'))
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
}

async function decide(target: AttachmentDecision) {
  if (!detail.value) return
  deciding.value = true
  try {
    await decideAttachment(detail.value.oid, { status: target })
    ElMessage.success(target === 'verified' ? '已通过' : '已驳回')
    detailOpen.value = false
    await load()
  } catch (e) {
    ElMessage.error(errorMessage(e, '裁决失败'))
  } finally {
    deciding.value = false
  }
}

function go(delta: number) {
  const next = Math.min(pages.value, Math.max(1, page.value + delta))
  if (next !== page.value) {
    page.value = next
    void load()
  }
}

onMounted(load)
</script>

<template>
  <section class="aa">
    <header class="aa__head">
      <div>
        <h2 class="aa__title">附件审核</h2>
        <p class="aa__hint">按状态与用途筛选；点行看 OCR 正文与事件流，裁决会留痕。</p>
      </div>
      <div class="aa__filters">
        <select v-model="status" class="yg2-input aa__select" @change="((page = 1), load())">
          <option value="">全部状态</option>
          <option value="pending">待审核</option>
          <option value="verified">已通过</option>
          <option value="rejected">已驳回</option>
        </select>
        <input
          v-model="purpose"
          class="yg2-input aa__input"
          placeholder="用途过滤，如 volunteer_order"
          @change="((page = 1), load())"
        />
        <button class="yg2-btn yg2-btn--quiet" type="button" @click="load">刷新</button>
      </div>
    </header>

    <p v-if="error" class="aa__error">{{ error }}</p>

    <div class="aa__tablewrap">
      <table class="aa__table">
        <thead>
          <tr>
            <th>文件名</th>
            <th>用途</th>
            <th>大小</th>
            <th>上传者</th>
            <th>上传时间</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.oid" tabindex="0" @click="openDetail(row)" @keydown.enter="openDetail(row)">
            <td class="aa__name">{{ row.name }}</td>
            <td>{{ row.purpose }}</td>
            <td class="aa__num">{{ formatBytes(row.size) }}</td>
            <td>{{ row.uploader_name || row.uploader_oid }}</td>
            <td class="aa__num">{{ row.created_at?.slice(0, 16).replace('T', ' ') }}</td>
            <td>
              <span class="aa__badge" :class="`aa__badge--${row.status}`">
                {{ STATUS_LABEL[row.status] ?? row.status }}
              </span>
            </td>
          </tr>
          <tr v-if="!loading && !rows.length">
            <td colspan="6" class="aa__empty">没有符合条件的附件</td>
          </tr>
          <tr v-if="loading">
            <td colspan="6" class="aa__empty">加载中…</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="aa__pager">
      <span>共 {{ total }} 条</span>
      <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" :disabled="page <= 1" @click="go(-1)">
        上一页
      </button>
      <span>{{ page }} / {{ pages }}</span>
      <button
        class="yg2-btn yg2-btn--quiet yg2-btn--sm"
        type="button"
        :disabled="page >= pages"
        @click="go(1)"
      >
        下一页
      </button>
    </footer>

    <Teleport to="body">
      <div v-if="detailOpen" class="aa-modal" @click.self="detailOpen = false">
        <div class="aa-modal__card">
          <header class="aa-modal__head">
            <h3 class="aa-modal__title">{{ detail?.name || '附件详情' }}</h3>
            <button class="aa-modal__close" type="button" aria-label="关闭" @click="detailOpen = false">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <path d="m6 6 12 12M18 6 6 18" />
              </svg>
            </button>
          </header>

          <div v-if="detailLoading" class="aa-modal__loading">读取中…</div>
          <template v-else-if="detail">
            <dl class="aa-modal__meta">
              <div><dt>用途</dt><dd>{{ detail.purpose }}</dd></div>
              <div><dt>大小</dt><dd>{{ formatBytes(detail.size) }}</dd></div>
              <div><dt>上传者</dt><dd>{{ detail.uploader_name || detail.uploader_oid }}</dd></div>
              <div><dt>状态</dt><dd>{{ STATUS_LABEL[detail.status] ?? detail.status }}</dd></div>
              <div><dt>OCR</dt><dd>{{ detail.ocr_status }}</dd></div>
            </dl>

            <section v-if="detail.ocr_text" class="aa-modal__section">
              <h4>OCR 正文</h4>
              <pre class="aa-modal__ocr">{{ detail.ocr_text }}</pre>
            </section>

            <section v-if="detail.events?.length" class="aa-modal__section">
              <h4>事件流</h4>
              <ul class="aa-modal__events">
                <li v-for="(ev, i) in detail.events" :key="i">
                  <span class="aa-modal__at">{{ ev.at?.slice(0, 16).replace('T', ' ') }}</span>
                  <strong>{{ ev.action }}</strong>
                  <span v-if="ev.actor" class="aa-modal__actor">{{ ev.actor }}</span>
                  <span v-if="ev.detail" class="aa-modal__detail">{{ ev.detail }}</span>
                </li>
              </ul>
            </section>

            <footer class="aa-modal__foot">
              <button class="yg2-btn yg2-btn--secondary" type="button" :disabled="deciding" @click="decide('rejected')">
                驳回
              </button>
              <button class="yg2-btn yg2-btn--primary" type="button" :disabled="deciding" @click="decide('verified')">
                通过
              </button>
            </footer>
          </template>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.aa {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.aa__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.aa__title {
  margin: 0 0 4px;
  font-size: 17px;
  font-weight: 600;
}

.aa__hint {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.aa__filters {
  display: flex;
  gap: 8px;
  align-items: center;
}

.aa__select,
.aa__input {
  height: 34px;
  width: 170px;
}

.aa__error {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-danger);
}

.aa__tablewrap {
  overflow-x: auto;
}

.aa__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}

.aa__table th {
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
  padding: 8px 10px;
  border-bottom: 1px solid var(--yg2-line);
  white-space: nowrap;
}

.aa__table td {
  padding: 10px;
  border-bottom: 1px solid var(--yg2-sep);
}

.aa__table tbody tr {
  cursor: pointer;
}

.aa__table tbody tr:hover {
  background: var(--yg2-surface-sunken);
}

.aa__name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aa__num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.aa__empty {
  text-align: center;
  color: var(--yg2-text-tertiary);
  padding: 24px 0;
}

.aa__badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-secondary);
}

.aa__badge--pending {
  background: color-mix(in srgb, var(--yg2-amber) 16%, transparent);
  color: color-mix(in srgb, var(--yg2-amber) 70%, var(--yg2-text));
}

.aa__badge--verified {
  background: var(--yg2-cat-community-soft);
  color: var(--yg2-cat-community);
}

.aa__badge--rejected {
  background: var(--yg2-danger-soft);
  color: var(--yg2-danger);
}

.aa__pager {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.aa-modal {
  position: fixed;
  inset: 0;
  z-index: 2400;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(6px);
}

.aa-modal__card {
  width: min(720px, 100%);
  max-height: 82dvh;
  overflow: auto;
  padding: 18px 20px 16px;
  border-radius: 24px;
  background: color-mix(in srgb, var(--yg2-surface) 94%, transparent);
  box-shadow: 0 30px 80px -24px rgba(0, 0, 0, 0.5);
}

.aa-modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.aa-modal__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aa-modal__close {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: 999px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-secondary);
  cursor: pointer;
}

.aa-modal__loading {
  padding: 24px 0;
  text-align: center;
  color: var(--yg2-text-tertiary);
}

.aa-modal__meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px 16px;
  margin: 0 0 14px;
}

.aa-modal__meta div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.aa-modal__meta dt {
  font-size: 11.5px;
  color: var(--yg2-text-tertiary);
}

.aa-modal__meta dd {
  margin: 0;
  font-size: 13.5px;
}

.aa-modal__section {
  margin-bottom: 14px;
}

.aa-modal__section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

.aa-modal__ocr {
  margin: 0;
  max-height: 200px;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--yg2-surface-sunken);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.aa-modal__events {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12.5px;
}

.aa-modal__events li {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}

.aa-modal__at,
.aa-modal__actor {
  color: var(--yg2-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.aa-modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>

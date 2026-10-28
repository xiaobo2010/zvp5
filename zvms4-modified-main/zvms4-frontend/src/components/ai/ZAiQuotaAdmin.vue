<script setup lang="ts">
/**
 * 额度管理（管理员 / quota.manage）。
 *
 * 三块内容：
 * 1. 用户配额表格：姓名/学号搜索 + 分页，直接看每个人的 LLM 与 OCR 用量；
 * 2. 重置申请审批：学生提交的申请在下方列表里逐条批准或驳回；
 * 3. 按 oid 精确操作：保留旧入口，查到的用户落在表格首行。
 *
 * 权限口径：GET /ai/quota/users 只对 admin 开放；`/ai/quota/requests` 对
 * admin 或 features['quota.manage'] 开放。两者都没有时整页只读提示。
 */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElRadio,
  ElRadioGroup,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { Refresh, Search, Setting } from '@element-plus/icons-vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import {
  AI_ERROR_FALLBACK_SECTION,
  aiErrorCopy,
  aiErrorOf,
  aiErrorSection,
  aiQuotaSourceLabelKey,
  approveQuotaRequest,
  listQuotaRequests,
  listQuotaUsers,
  rejectQuotaRequest,
  resetUserQuota,
  setUserLimit,
  userQuota,
  type AiQuotaApproveInput,
  type AiQuotaRequestAdminItem,
  type AiQuotaUsageView,
  type AiQuotaUserRow
} from '@/api/ai'
import { getMyRolePolicy } from '@/api/system/rolePolicies'
import { useUserStore } from '@/stores/user'
import { formatDateTime } from '@/utils/dateTime'
import type { AiQuotaKind } from '@/../types/ai'

type DecisionMode = 'approve' | 'reject'
type ApproveMode = 'reset' | 'reset_limit'

const { t } = useI18n()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))
/** 非 admin 的角色可能被授予 quota.manage；没拿到策略前按无权处理。 */
const policyAllowsQuota = ref(false)
const canManage = computed(() => isAdmin.value || policyAllowsQuota.value)

// ---------------------------------------------------------------------------
// 用户配额表格
// ---------------------------------------------------------------------------

const users = ref<AiQuotaUserRow[]>([])
const usersTotal = ref(0)
const usersPage = ref(1)
const usersSize = ref(20)
const usersLoading = ref(false)
const usersError = ref('')
const keyword = ref('')

/** 「按 oid 精确操作」查到的那个用户，置顶显示。 */
const quickRow = ref<AiQuotaUserRow | null>(null)

/** 精确查到的用户排在首行；与表格里的同一个人只保留一份。 */
const displayUsers = computed<AiQuotaUserRow[]>(() => {
  const quick = quickRow.value
  if (!quick) {
    return users.value
  }
  return [quick, ...users.value.filter((row) => row.oid !== quick.oid)]
})

function kindLabel(kind: AiQuotaKind): string {
  return kind === 'chat' ? t('ai.quotaAdmin.chatTitle') : t('ai.quotaAdmin.ocrTitle')
}

/** 来源标签文案；老版本后端没给 source 时返回 null，不渲染标签。 */
function sourceLabel(usage: AiQuotaUsageView | null | undefined): string | null {
  const key = aiQuotaSourceLabelKey(usage?.source)
  return key ? t(key) : null
}

async function loadUsers(page = usersPage.value) {
  if (!isAdmin.value) {
    return
  }
  usersLoading.value = true
  usersError.value = ''
  try {
    const data = await listQuotaUsers({
      query: keyword.value.trim(),
      page,
      size: usersSize.value
    })
    users.value = data.items
    usersTotal.value = data.total
    usersPage.value = data.page
  } catch (error) {
    users.value = []
    usersTotal.value = 0
    usersError.value = errorText(error, 'ai.quotaAdmin.usersLoadFailed')
  } finally {
    usersLoading.value = false
  }
}

function search() {
  usersPage.value = 1
  void loadUsers(1)
}

function onUsersPageChange(page: number) {
  usersPage.value = page
  void loadUsers(page)
}

// ---------------------------------------------------------------------------
// 重置申请
// ---------------------------------------------------------------------------

const requests = ref<AiQuotaRequestAdminItem[]>([])
const requestsTotal = ref(0)
const requestsPage = ref(1)
const requestsSize = ref(20)
const requestsLoading = ref(false)
const requestsError = ref('')

async function loadRequests(page = requestsPage.value) {
  if (!canManage.value) {
    return
  }
  requestsLoading.value = true
  requestsError.value = ''
  try {
    const data = await listQuotaRequests({ status: 'pending', page, size: requestsSize.value })
    requests.value = data.items
    requestsTotal.value = data.total
    requestsPage.value = data.page
  } catch (error) {
    requests.value = []
    requestsTotal.value = 0
    requestsError.value = errorText(error, 'ai.quotaAdmin.requests.loadFailed')
  } finally {
    requestsLoading.value = false
  }
}

function onRequestsPageChange(page: number) {
  requestsPage.value = page
  void loadRequests(page)
}

async function refreshAll() {
  await Promise.all([loadUsers(), loadRequests()])
}

function errorText(error: unknown, fallbackKey: string): string {
  const aiError = aiErrorOf(error)
  if (!aiError) {
    return error instanceof Error && error.message ? error.message : t(fallbackKey)
  }
  if (aiError.message) {
    return aiError.message
  }
  // 申请相关错误码还没有人话映射：展示后端 detail 原文，别折叠成通用文案。
  if (aiErrorSection(aiError.code) === AI_ERROR_FALLBACK_SECTION) {
    return aiError.code
  }
  const copy = aiErrorCopy(aiError.code, t)
  return `${copy.title}：${copy.hint}`
}

// ---------------------------------------------------------------------------
// 改上限 / 清零
// ---------------------------------------------------------------------------

const limitVisible = ref(false)
const limitOid = ref('')
const limitUserLabel = ref('')
const limitKind = ref<AiQuotaKind>('chat')
const limitValue = ref(0)
const limitSaving = ref(false)
const quickLookupVisible = ref(false)
const quickLookupOid = ref('')
const quickLookupLoading = ref(false)

function openLimit(row: AiQuotaUserRow, kind: AiQuotaKind) {
  limitOid.value = row.oid
  limitUserLabel.value = row.name
  limitKind.value = kind
  limitValue.value = row.quotas[kind]?.limit ?? 0
  limitVisible.value = true
}

async function submitLimit() {
  if (limitSaving.value || !limitOid.value) {
    return
  }
  limitSaving.value = true
  try {
    await setUserLimit(limitOid.value, limitKind.value, limitValue.value)
    limitVisible.value = false
    ElMessage.success(t('ai.quotaAdmin.savedNotice'))
    await refreshAll()
  } catch (error) {
    ElMessage.error(errorText(error, 'ai.quotaAdmin.saveFailed'))
  } finally {
    limitSaving.value = false
  }
}

async function resetUsage(row: AiQuotaUserRow, kind: AiQuotaKind) {
  try {
    await ElMessageBox.confirm(
      t('ai.quotaAdmin.resetConfirm'),
      t('ai.quotaAdmin.resetConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('ai.common.confirm'),
        cancelButtonText: t('ai.common.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await resetUserQuota(row.oid, kind)
    ElMessage.success(t('ai.quotaAdmin.resetNotice'))
    await refreshAll()
  } catch (error) {
    ElMessage.error(errorText(error, 'ai.quotaAdmin.resetFailed'))
  }
}

function openQuickLookup() {
  quickLookupOid.value = ''
  quickLookupVisible.value = true
}

async function submitQuickLookup() {
  const oid = quickLookupOid.value.trim()
  if (!oid || quickLookupLoading.value) {
    return
  }
  quickLookupLoading.value = true
  try {
    const snapshot = await userQuota(oid)
    quickRow.value = {
      oid,
      name: oid,
      business_id: null,
      quotas: {
        chat: { used: snapshot.chat.used, limit: snapshot.chat.limit, source: snapshot.chat.source },
        ocr: { used: snapshot.ocr.used, limit: snapshot.ocr.limit, source: snapshot.ocr.source }
      },
      pending_request: false
    }
    quickLookupVisible.value = false
    ElMessage.success(t('ai.quotaAdmin.quickLookupDone'))
  } catch (error) {
    ElMessage.error(errorText(error, 'ai.quotaAdmin.quickLookupFailed'))
  } finally {
    quickLookupLoading.value = false
  }
}

// ---------------------------------------------------------------------------
// 审批
// ---------------------------------------------------------------------------

const decisionVisible = ref(false)
const decisionMode = ref<DecisionMode>('approve')
const decisionTarget = ref<AiQuotaRequestAdminItem | null>(null)
const approveMode = ref<ApproveMode>('reset')
const approveLimit = ref(0)
const rejectNote = ref('')
const deciding = ref(false)

function openApprove(item: AiQuotaRequestAdminItem) {
  decisionMode.value = 'approve'
  decisionTarget.value = item
  approveMode.value = 'reset'
  approveLimit.value = item.requested_limit && item.requested_limit > 0 ? item.requested_limit : 0
  rejectNote.value = ''
  decisionVisible.value = true
}

function openReject(item: AiQuotaRequestAdminItem) {
  decisionMode.value = 'reject'
  decisionTarget.value = item
  approveMode.value = 'reset'
  approveLimit.value = 0
  rejectNote.value = ''
  decisionVisible.value = true
}

async function submitDecision() {
  const target = decisionTarget.value
  if (!target || deciding.value) {
    return
  }
  deciding.value = true
  try {
    if (decisionMode.value === 'approve') {
      const input: AiQuotaApproveInput = { reset: true }
      if (approveMode.value === 'reset_limit') {
        if (approveLimit.value <= 0) {
          ElMessage.warning(t('ai.quotaAdmin.requests.approveLimitRequired'))
          return
        }
        input.limit = Math.trunc(approveLimit.value)
      }
      await approveQuotaRequest(target.oid, input)
      ElMessage.success(t('ai.quotaAdmin.requests.approvedNotice'))
    } else {
      await rejectQuotaRequest(target.oid, rejectNote.value.trim() || undefined)
      ElMessage.success(t('ai.quotaAdmin.requests.rejectedNotice'))
    }
    decisionVisible.value = false
    await refreshAll()
  } catch (error) {
    ElMessage.error(errorText(error, 'ai.quotaAdmin.requests.decisionFailed'))
  } finally {
    deciding.value = false
  }
}

onMounted(async () => {
  if (isAdmin.value) {
    await Promise.all([loadUsers(1), loadRequests(1)])
    return
  }
  try {
    const policy = await getMyRolePolicy()
    policyAllowsQuota.value = policy.features['quota.manage'] === true
  } catch {
    policyAllowsQuota.value = false
  }
  if (canManage.value) {
    await loadRequests(1)
  }
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <section v-if="!canManage" class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.quotaAdmin.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.subtitle') }}</p>
        </div>
      </header>
      <div class="yg-card__body">
        <p class="yg-text-sm yg-text-danger">{{ t('ai.quotaAdmin.noPermission') }}</p>
        <p class="yg-text-xs yg-text-tertiary mt-1">{{ t('ai.quotaAdmin.noPermissionHint') }}</p>
      </div>
    </section>

    <template v-else>
      <section class="yg-card">
        <header class="yg-card__header">
          <div class="flex flex-col gap-1 min-w-0">
            <h2 class="yg-card__title">{{ t('ai.quotaAdmin.title') }}</h2>
            <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.subtitle') }}</p>
          </div>
          <div class="yg-card__extra">
            <ElButton @click="openQuickLookup">
              <ElIcon class="mr-1"><Setting /></ElIcon>
              {{ t('ai.quotaAdmin.quickLookup') }}
            </ElButton>
            <ElButton :loading="usersLoading || requestsLoading" @click="refreshAll">
              <ElIcon class="mr-1"><Refresh /></ElIcon>
              {{ t('ai.common.refresh') }}
            </ElButton>
          </div>
        </header>

        <div class="yg-card__body">
          <div v-if="isAdmin" class="flex flex-wrap items-center gap-2">
            <ElInput
              v-model="keyword"
              class="w-64"
              clearable
              :placeholder="t('ai.quotaAdmin.searchPlaceholder')"
              @keyup.enter="search"
            />
            <ElButton type="primary" plain @click="search">
              <ElIcon class="mr-1"><Search /></ElIcon>
              {{ t('ai.quotaAdmin.search') }}
            </ElButton>
            <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.monthlyNotice') }}</p>
          </div>
          <p v-else class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.requests.hint') }}</p>
        </div>
      </section>

      <section v-if="isAdmin" class="yg-card" v-loading="usersLoading">
        <header class="yg-card__header">
          <div class="flex flex-col gap-1 min-w-0">
            <h2 class="yg-statistic__title">{{ t('ai.quotaAdmin.users.title') }}</h2>
            <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.users.hint') }}</p>
          </div>
        </header>

        <div class="yg-card__body">
          <div v-if="usersError" class="py-6 flex flex-col items-center gap-3">
            <p class="yg-text-sm yg-text-danger">{{ usersError }}</p>
            <ElButton size="small" :loading="usersLoading" @click="loadUsers()">
              {{ t('ai.quotaAdmin.retry') }}
            </ElButton>
          </div>
          <p
            v-else-if="!usersLoading && !displayUsers.length"
            class="py-8 text-center yg-text-sm yg-text-tertiary"
          >
            {{ t('ai.quotaAdmin.users.empty') }}
          </p>
          <ElTable v-else :data="displayUsers" class="w-full">
            <ElTableColumn :label="t('ai.quotaAdmin.users.user')" min-width="160">
              <template #default="{ row }">
                <div class="flex flex-col gap-1">
                  <div class="flex items-center gap-2">
                    <span class="yg-text-sm">{{ row.name }}</span>
                    <ElTag v-if="row.pending_request" type="warning" size="small" effect="plain">
                      {{ t('ai.quotaAdmin.users.pendingBadge') }}
                    </ElTag>
                  </div>
                  <span class="yg-text-xs yg-text-tertiary tabular-nums">
                    {{ row.business_id ?? row.oid }}
                  </span>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.users.chat')" min-width="110">
              <template #default="{ row }">
                <span class="tabular-nums">
                  {{ row.quotas.chat.used }} / {{ row.quotas.chat.limit }}
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.users.ocr')" min-width="110">
              <template #default="{ row }">
                <span class="tabular-nums">
                  {{ row.quotas.ocr.used }} / {{ row.quotas.ocr.limit }}
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.users.source')" min-width="150">
              <template #default="{ row }">
                <div class="flex flex-wrap items-center gap-1">
                  <ElTag
                    v-if="sourceLabel(row.quotas.chat)"
                    type="info"
                    size="small"
                    effect="plain"
                  >
                    {{ t('ai.quotaAdmin.users.chatShort') }}：{{ sourceLabel(row.quotas.chat) }}
                  </ElTag>
                  <ElTag
                    v-if="sourceLabel(row.quotas.ocr)"
                    type="info"
                    size="small"
                    effect="plain"
                  >
                    {{ t('ai.quotaAdmin.users.ocrShort') }}：{{ sourceLabel(row.quotas.ocr) }}
                  </ElTag>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.common.actions')" min-width="240">
              <template #default="{ row }">
                <div class="flex flex-wrap items-center gap-1">
                  <ElButton size="small" text type="primary" @click="openLimit(row, 'chat')">
                    {{ t('ai.quotaAdmin.users.changeChatLimit') }}
                  </ElButton>
                  <ElButton size="small" text type="primary" @click="openLimit(row, 'ocr')">
                    {{ t('ai.quotaAdmin.users.changeOcrLimit') }}
                  </ElButton>
                  <ElButton size="small" text type="danger" @click="resetUsage(row, 'chat')">
                    {{ t('ai.quotaAdmin.users.resetChat') }}
                  </ElButton>
                  <ElButton size="small" text type="danger" @click="resetUsage(row, 'ocr')">
                    {{ t('ai.quotaAdmin.users.resetOcr') }}
                  </ElButton>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
          <ZPagination
            v-if="usersTotal > 0"
            v-model:current-page="usersPage"
            v-model:page-size="usersSize"
            :total="usersTotal"
            hide-on-single-page
            @change="onUsersPageChange"
          />
        </div>
      </section>

      <section class="yg-card" v-loading="requestsLoading">
        <header class="yg-card__header">
          <div class="flex flex-col gap-1 min-w-0">
            <h2 class="yg-statistic__title">{{ t('ai.quotaAdmin.requests.title') }}</h2>
            <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.requests.hint') }}</p>
          </div>
          <div class="yg-card__extra">
            <ElButton :loading="requestsLoading" @click="loadRequests()">
              <ElIcon class="mr-1"><Refresh /></ElIcon>
              {{ t('ai.common.refresh') }}
            </ElButton>
          </div>
        </header>

        <div class="yg-card__body">
          <div v-if="requestsError" class="py-6 flex flex-col items-center gap-3">
            <p class="yg-text-sm yg-text-danger">{{ requestsError }}</p>
            <ElButton size="small" :loading="requestsLoading" @click="loadRequests()">
              {{ t('ai.quotaAdmin.retry') }}
            </ElButton>
          </div>
          <p
            v-else-if="!requestsLoading && !requests.length"
            class="py-8 text-center yg-text-sm yg-text-tertiary"
          >
            {{ t('ai.quotaAdmin.requests.empty') }}
          </p>
          <ElTable v-else :data="requests" class="w-full">
            <ElTableColumn :label="t('ai.quotaAdmin.requests.user')" min-width="150">
              <template #default="{ row }">
                <div class="flex flex-col gap-1">
                  <span class="yg-text-sm">{{ row.user_name }}</span>
                  <span class="yg-text-xs yg-text-tertiary tabular-nums">
                    {{ row.business_id ?? row.user_oid }}
                  </span>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.requests.kind')" min-width="90">
              <template #default="{ row }">
                <ElTag size="small" effect="plain">{{ kindLabel(row.kind) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.requests.reason')" min-width="180">
              <template #default="{ row }">
                <span class="yg-text-sm">{{ row.reason || t('ai.common.none') }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.requests.createdAt')" min-width="140">
              <template #default="{ row }">
                <span class="tabular-nums">{{ formatDateTime(row.created_at) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.quotaAdmin.requests.requestedLimit')" min-width="100">
              <template #default="{ row }">
                <span class="tabular-nums">{{ row.requested_limit ?? t('ai.common.none') }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.common.actions')" min-width="140">
              <template #default="{ row }">
                <div class="flex flex-wrap items-center gap-1">
                  <ElButton size="small" type="primary" plain @click="openApprove(row)">
                    {{ t('ai.quotaAdmin.requests.approve') }}
                  </ElButton>
                  <ElButton size="small" text type="danger" @click="openReject(row)">
                    {{ t('ai.quotaAdmin.requests.reject') }}
                  </ElButton>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
          <ZPagination
            v-if="requestsTotal > 0"
            v-model:current-page="requestsPage"
            v-model:page-size="requestsSize"
            :total="requestsTotal"
            hide-on-single-page
            @change="onRequestsPageChange"
          />
        </div>
      </section>
    </template>

    <ElDialog
      v-model="limitVisible"
      :title="t('ai.quotaAdmin.setLimitTitle')"
      width="min(420px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!limitSaving"
      :show-close="!limitSaving"
      append-to-body
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ai.quotaAdmin.users.user')">
          <span class="yg-text-sm tabular-nums">
            {{ limitUserLabel }}（{{ limitOid }}）
          </span>
        </ElFormItem>
        <ElFormItem :label="t('ai.quotaAdmin.limitKind')">
          <ElSelect v-model="limitKind" class="w-full" :disabled="limitSaving">
            <ElOption :label="t('ai.quotaAdmin.chatTitle')" value="chat" />
            <ElOption :label="t('ai.quotaAdmin.ocrTitle')" value="ocr" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="t('ai.quotaAdmin.limitValue')">
          <ElInputNumber
            v-model="limitValue"
            :min="0"
            :step="10"
            class="w-full"
            :disabled="limitSaving"
          />
          <p class="yg-text-xs yg-text-tertiary mt-1">{{ t('ai.quotaAdmin.limitHint') }}</p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="limitSaving" @click="limitVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="limitSaving" @click="submitLimit">
            {{ t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <ElDialog
      v-model="quickLookupVisible"
      :title="t('ai.quotaAdmin.quickLookupTitle')"
      width="min(420px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!quickLookupLoading"
      :show-close="!quickLookupLoading"
      append-to-body
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ai.quotaAdmin.quickLookupOid')">
          <ElInput
            v-model="quickLookupOid"
            :placeholder="t('ai.quotaAdmin.quickLookupPlaceholder')"
            :disabled="quickLookupLoading"
          />
          <p class="yg-text-xs yg-text-tertiary mt-1">
            {{ t('ai.quotaAdmin.quickLookupHint') }}
          </p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="quickLookupLoading" @click="quickLookupVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="quickLookupLoading" @click="submitQuickLookup">
            {{ t('ai.quotaAdmin.search') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <ElDialog
      v-model="decisionVisible"
      :title="
        decisionMode === 'approve'
          ? t('ai.quotaAdmin.requests.approveTitle')
          : t('ai.quotaAdmin.requests.rejectTitle')
      "
      width="min(460px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!deciding"
      :show-close="!deciding"
      append-to-body
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ai.quotaAdmin.requests.user')">
          <span class="yg-text-sm">
            {{ decisionTarget?.user_name ?? '' }}
            <span class="yg-text-xs yg-text-tertiary tabular-nums">
              （{{ decisionTarget?.business_id ?? decisionTarget?.user_oid ?? '' }}）
            </span>
          </span>
        </ElFormItem>

        <template v-if="decisionMode === 'approve'">
          <ElFormItem :label="t('ai.quotaAdmin.requests.approveMode')">
            <ElRadioGroup v-model="approveMode" :disabled="deciding">
              <ElRadio value="reset">{{ t('ai.quotaAdmin.requests.approveResetOnly') }}</ElRadio>
              <ElRadio value="reset_limit">
                {{ t('ai.quotaAdmin.requests.approveResetLimit') }}
              </ElRadio>
            </ElRadioGroup>
          </ElFormItem>
          <ElFormItem
            v-if="approveMode === 'reset_limit'"
            :label="t('ai.quotaAdmin.requests.newLimit')"
          >
            <ElInputNumber v-model="approveLimit" :min="1" :step="10" class="w-full" />
            <p class="yg-text-xs yg-text-tertiary mt-1">
              {{ t('ai.quotaAdmin.requests.newLimitHint') }}
            </p>
          </ElFormItem>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.requests.approveHint') }}</p>
        </template>

        <ElFormItem v-else :label="t('ai.quotaAdmin.requests.rejectNote')">
          <ElInput
            v-model="rejectNote"
            type="textarea"
            :rows="3"
            :placeholder="t('ai.quotaAdmin.requests.rejectNotePlaceholder')"
            :disabled="deciding"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="deciding" @click="decisionVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton
            :type="decisionMode === 'approve' ? 'primary' : 'danger'"
            :loading="deciding"
            @click="submitDecision"
          >
            {{
              decisionMode === 'approve'
                ? t('ai.quotaAdmin.requests.approve')
                : t('ai.quotaAdmin.requests.reject')
            }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

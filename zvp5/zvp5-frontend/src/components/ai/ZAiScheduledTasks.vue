<script setup lang="ts">
/**
 * AI 定时任务。
 *
 * 一句话规则：**只有管理员审核通过（approved）的提示词才会被执行**，
 * 而且用的是任务创建者自己的可用 Key 与本月额度。所以这一页必须把三件事
 * 摆在明面上：审核状态、执行结论（尤其是「为什么跳过」）、以及配额。
 *
 * 与其它 AI 页保持同一套语言：卡片 + 表格 + 抽屉 + ElMessage 错误提示，
 * 窄屏把表格换成卡片，不另起一套样式。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElDialog,
  ElDrawer,
  ElEmpty,
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
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTooltip
} from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Delete, Edit, InfoFilled, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import ZSelectPerson from '@/components/form/ZSelectPerson.vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import type { AiScheduledTasksQuery } from '@/api/ai/schedule'
import {
  AI_SCHEDULE_CRON_MAX,
  AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES,
  AI_SCHEDULE_DEPARTMENT_QUOTA,
  AI_SCHEDULE_MIN_INTERVAL_MINUTES,
  AI_SCHEDULE_NAME_MAX,
  AI_SCHEDULE_PROMPT_MAX,
  aiErrorCodeOf,
  aiErrorCopy,
  createAiScheduledTask,
  deleteAiScheduledTask,
  isValidCronExpression,
  listAiScheduledTaskRuns,
  listAiScheduledTasks,
  reviewAiScheduledTask,
  runAiScheduledTaskNow,
  updateAiScheduledTask
} from '@/api/ai'
import { useUserStore } from '@/stores/user'
import { formatDateTime } from '@/utils/dateTime'
import type {
  AiScheduledLastStatus,
  AiScheduledReviewDecision,
  AiScheduledReviewStatus,
  AiScheduledScheduleKind,
  AiScheduledTask,
  AiScheduledTaskRun,
  AiScheduledTaskUpdateInput
} from '@/../types/ai'

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'
type ReviewFilter = 'all' | AiScheduledReviewStatus

const { t } = useI18n()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))
const isDepartment = computed(() => userStore.position.includes('department'))
/** 后端只允许 admin 与 department 建任务（_require_creator）。 */
const canCreate = computed(() => isAdmin.value || isDepartment.value)

const { width } = useWindowSize()
const narrow = computed(() => width.value <= 768)

const REVIEW_TAG_TYPE: Record<AiScheduledReviewStatus, TagType> = {
  draft: 'info',
  pending: 'warning',
  approved: 'success',
  rejected: 'danger'
}

const LAST_TAG_TYPE: Record<AiScheduledLastStatus, TagType> = {
  queued: 'primary',
  failed: 'danger',
  skipped_review: 'warning',
  skipped_busy: 'warning',
  skipped_no_key: 'warning',
  skipped_quota: 'warning',
  skipped_owner: 'warning',
  skipped_flagged: 'warning'
}

// ---------------------------------------------------------------------------
// 列表
// ---------------------------------------------------------------------------

const rows = ref<AiScheduledTask[]>([])
const loading = ref(false)
const loadingMore = ref(false)
const loadFailed = ref(false)

/** admin 专用：按 owner oid 看某个人的任务；空 = 全部用户。 */
const ownerFilter = ref('')
/** 审核状态过滤：交给服务端（review_status），不在前端假装过滤。 */
const reviewFilter = ref<ReviewFilter>('all')
/** 已加载的条数；「加载更多」用它算 offset。 */
const offset = ref(0)
/** 还有下一页：当前页条数打满 limit 才算。 */
const hasMore = ref(false)

/**
 * 管理端视图每页条数。
 *
 * 50 是「加载更多」按钮能一直有意义的上限：后端默认一页 200 条，一口气拉完
 * 200 行再让人翻页没有意义。
 */
const PAGE_LIMIT = 50

let listSeq = 0

function buildQuery(reset: boolean): AiScheduledTasksQuery {
  // 非 admin 只能看自己的任务：管理端开关一律不下发（后端会 403）。
  if (!isAdmin.value) {
    return {}
  }
  const query: AiScheduledTasksQuery = {
    allTasks: true,
    limit: PAGE_LIMIT,
    offset: reset ? 0 : offset.value
  }
  if (reviewFilter.value !== 'all') {
    query.reviewStatus = reviewFilter.value
  }
  const owner = ownerFilter.value.trim()
  if (owner) {
    query.ownerOid = owner
  }
  return query
}

async function fetchTasks(reset: boolean): Promise<void> {
  const seq = ++listSeq
  const query = buildQuery(reset)
  try {
    const result = await listAiScheduledTasks(query)
    if (seq !== listSeq) {
      return
    }
    rows.value = reset ? result.rows : [...rows.value, ...result.rows]
    offset.value = (reset ? 0 : offset.value) + result.rows.length
    // 只有管理端视图是分页的；本人的列表一次就全给了。
    hasMore.value = Boolean(query.allTasks) && result.size >= PAGE_LIMIT
    loadFailed.value = false
  } catch (error) {
    if (seq !== listSeq) {
      return
    }
    if (reset) {
      rows.value = []
      hasMore.value = false
      loadFailed.value = true
    }
    reportError(error)
  }
}

async function load() {
  loading.value = true
  try {
    await fetchTasks(true)
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  // 刷新进行中时不许追加：两者的响应序号会互相顶掉，追加会把刚刷新的结果盖掉。
  if (loadingMore.value || loading.value || !hasMore.value) {
    return
  }
  loadingMore.value = true
  try {
    await fetchTasks(false)
  } finally {
    loadingMore.value = false
  }
}

const emptyDescription = computed(() =>
  loadFailed.value ? t('ai.schedule.loadFailed') : t('ai.schedule.empty')
)

/**
 * 配额提示：部门角色显示已用条数，管理员不限。
 *
 * 已用条数的口径与后端一致：**被驳回的任务不占额度**
 * （后端 count_active_for_owner 只数 review_status != 'rejected' 的行）。
 */
const quotaUsed = computed(
  () => rows.value.filter((row) => row.review_status !== 'rejected').length
)

const quotaText = computed(() =>
  isAdmin.value
    ? t('ai.schedule.quotaUnlimited')
    : t('ai.schedule.quotaUsed', {
        used: quotaUsed.value,
        limit: AI_SCHEDULE_DEPARTMENT_QUOTA
      })
)

function onOwnerChange(value: string | string[]) {
  ownerFilter.value = typeof value === 'string' ? value.trim() : ''
  void load()
}

/** 审核状态改的是服务端的查询条件，所以要重新拉第一页。 */
function onReviewFilterChange() {
  void load()
}

function reviewStatusLabel(row: AiScheduledTask): string {
  return t(`ai.schedule.review.${row.review_status}`)
}

function reviewTagType(row: AiScheduledTask): TagType {
  return REVIEW_TAG_TYPE[row.review_status] ?? 'info'
}

function lastStatusLabel(row: AiScheduledTask): string {
  return row.last_status ? t(`ai.schedule.last.${row.last_status}`) : t('ai.schedule.last.none')
}

function lastTagType(row: AiScheduledTask): TagType {
  return row.last_status ? (LAST_TAG_TYPE[row.last_status] ?? 'info') : 'info'
}

function lastStatusHint(row: AiScheduledTask): string {
  return row.last_status ? t(`ai.schedule.lastHint.${row.last_status}`) : ''
}

function planLabel(row: AiScheduledTask): string {
  if (row.schedule_kind === 'cron') {
    return t('ai.schedule.plan.cron', { expr: row.cron_expr || '—' })
  }
  return t('ai.schedule.plan.interval', { minutes: row.interval_minutes })
}

function replaceRow(updated: AiScheduledTask) {
  const index = rows.value.findIndex((item) => item.oid === updated.oid)
  if (index >= 0) {
    rows.value[index] = updated
  } else {
    rows.value = [updated, ...rows.value]
  }
}

// ---------------------------------------------------------------------------
// 错误文案
// ---------------------------------------------------------------------------

function reportError(error: unknown) {
  const code = aiErrorCodeOf(error)
  if (code === 'AI_SCHEDULE_QUOTA_EXCEEDED') {
    ElMessage.warning(t('ai.schedule.quotaExceeded'))
    return
  }
  const copy = aiErrorCopy(code, t)
  ElMessage.error(`${copy.title}：${copy.hint}`)
}

function applyServerError(error: unknown) {
  const code = aiErrorCodeOf(error)
  if (code === 'AI_SCHEDULE_QUOTA_EXCEEDED') {
    fieldError.value = { form: t('ai.schedule.quotaExceeded') }
    return
  }
  const copy = aiErrorCopy(code, t)
  fieldError.value = { form: `${copy.title}：${copy.hint}` }
}

// ---------------------------------------------------------------------------
// 新建 / 编辑
// ---------------------------------------------------------------------------

type DialogMode = 'create' | 'edit'

const mode = ref<DialogMode>('create')
const dialogVisible = ref(false)
const editingId = ref('')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const fieldError = ref<Record<string, string>>({})
/** 原提示词：用来判断「是否改了文案」（改了就会重新进入审核）。 */
const originalPrompt = ref('')

const form = reactive({
  name: '',
  promptText: '',
  scheduleKind: 'interval' as AiScheduledScheduleKind,
  intervalMinutes: AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES,
  cronExpr: '0 9 * * *',
  model: '',
  enabled: true
})

const dialogTitle = computed(() =>
  mode.value === 'create' ? t('ai.schedule.formCreate') : t('ai.schedule.formEdit')
)

/** 提示词被改动过：提示用户这条任务会重新排队等审核。 */
const promptChanged = computed(
  () => mode.value === 'edit' && form.promptText.trim() !== originalPrompt.value
)

const promptCount = computed(() =>
  t('ai.schedule.fields.promptCount', {
    count: form.promptText.length,
    max: AI_SCHEDULE_PROMPT_MAX
  })
)

function validateName(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const text = String(value ?? '').trim()
  if (!text) {
    callback(new Error(t('ai.schedule.validation.nameRequired')))
    return
  }
  if (text.length > AI_SCHEDULE_NAME_MAX) {
    callback(new Error(t('ai.schedule.validation.nameTooLong')))
    return
  }
  callback()
}

function validatePrompt(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const text = String(value ?? '')
  if (!text.trim()) {
    callback(new Error(t('ai.schedule.validation.promptRequired')))
    return
  }
  if (text.length > AI_SCHEDULE_PROMPT_MAX) {
    callback(new Error(t('ai.schedule.validation.promptTooLong')))
    return
  }
  callback()
}

function validateInterval(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const minutes = Number(value)
  if (!Number.isInteger(minutes) || minutes < AI_SCHEDULE_MIN_INTERVAL_MINUTES) {
    callback(new Error(t('ai.schedule.validation.intervalInvalid')))
    return
  }
  callback()
}

function validateCron(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (form.scheduleKind !== 'cron') {
    callback()
    return
  }
  if (!isValidCronExpression(String(value ?? ''))) {
    callback(new Error(t('ai.schedule.validation.cronInvalid')))
    return
  }
  callback()
}

const formRules: FormRules = {
  name: [{ validator: validateName, trigger: 'blur' }],
  promptText: [{ validator: validatePrompt, trigger: 'blur' }],
  intervalMinutes: [{ validator: validateInterval, trigger: 'blur' }],
  cronExpr: [{ validator: validateCron, trigger: 'blur' }]
}

function resetForm() {
  form.name = ''
  form.promptText = ''
  form.scheduleKind = 'interval'
  form.intervalMinutes = AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES
  form.cronExpr = '0 9 * * *'
  form.model = ''
  form.enabled = true
  originalPrompt.value = ''
  fieldError.value = {}
}

function openCreate() {
  mode.value = 'create'
  editingId.value = ''
  resetForm()
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row: AiScheduledTask) {
  mode.value = 'edit'
  editingId.value = row.oid
  resetForm()
  form.name = row.name
  form.promptText = row.prompt_text
  form.scheduleKind = row.schedule_kind
  form.intervalMinutes = row.interval_minutes || AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES
  form.cronExpr = row.cron_expr || '0 9 * * *'
  form.model = row.model ?? ''
  form.enabled = row.enabled
  originalPrompt.value = row.prompt_text
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

async function submit() {
  if (submitting.value || !formRef.value) {
    return
  }
  submitting.value = true
  fieldError.value = {}
  try {
    const valid = await formRef.value.validate().catch(() => false)
    if (!valid) {
      return
    }
    const name = form.name.trim()
    const promptText = form.promptText.trim()
    const model = form.model.trim()

    if (mode.value === 'create') {
      await createAiScheduledTask({
        name,
        prompt_text: promptText,
        schedule_kind: form.scheduleKind,
        interval_minutes: form.intervalMinutes,
        cron_expr: form.scheduleKind === 'cron' ? form.cronExpr.trim() : null,
        model: model || null
      })
      dialogVisible.value = false
      ElMessage.success(t('ai.schedule.createdNotice'))
    } else {
      const original = rows.value.find((row) => row.oid === editingId.value)
      if (!original) {
        dialogVisible.value = false
        return
      }
      const payload: AiScheduledTaskUpdateInput = {}
      if (name !== original.name) {
        payload.name = name
      }
      if (promptText !== original.prompt_text.trim()) {
        payload.prompt_text = promptText
      }
      if (form.scheduleKind !== original.schedule_kind) {
        payload.schedule_kind = form.scheduleKind
      }
      if (
        form.scheduleKind === 'interval' &&
        form.intervalMinutes !== original.interval_minutes
      ) {
        payload.interval_minutes = form.intervalMinutes
      }
      if (
        form.scheduleKind === 'cron' &&
        form.cronExpr.trim() !== (original.cron_expr ?? '')
      ) {
        payload.cron_expr = form.cronExpr.trim()
      }
      if (model !== (original.model ?? '')) {
        payload.model = model || null
      }
      if (form.enabled !== original.enabled) {
        payload.enabled = form.enabled
      }
      await updateAiScheduledTask(editingId.value, payload)
      dialogVisible.value = false
      ElMessage.success(t('ai.schedule.savedNotice'))
    }
    await load()
  } catch (error) {
    applyServerError(error)
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------------------
// 启停 / 立即执行 / 删除
// ---------------------------------------------------------------------------

const togglingId = ref('')
const runningId = ref('')
const deletingId = ref('')

async function toggleEnabled(row: AiScheduledTask) {
  if (togglingId.value) {
    return
  }
  togglingId.value = row.oid
  try {
    const updated = await updateAiScheduledTask(row.oid, { enabled: !row.enabled })
    replaceRow(updated)
    ElMessage.success(updated.enabled ? t('ai.schedule.enabledOn') : t('ai.schedule.enabledOff'))
  } catch (error) {
    reportError(error)
  } finally {
    togglingId.value = ''
  }
}

async function runNow(row: AiScheduledTask) {
  if (row.review_status !== 'approved' || runningId.value) {
    return
  }
  runningId.value = row.oid
  try {
    await runAiScheduledTaskNow(row.oid)
    ElMessage.success(t('ai.schedule.runNotice'))
    await load()
  } catch (error) {
    reportError(error)
  } finally {
    runningId.value = ''
  }
}

async function remove(row: AiScheduledTask) {
  if (deletingId.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      t('ai.schedule.deleteConfirm', { name: row.name }),
      t('ai.schedule.deleteConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('ai.common.delete'),
        cancelButtonText: t('ai.common.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  deletingId.value = row.oid
  try {
    await deleteAiScheduledTask(row.oid)
    ElMessage.success(t('ai.schedule.deletedNotice'))
    await load()
  } catch (error) {
    reportError(error)
  } finally {
    deletingId.value = ''
  }
}

// ---------------------------------------------------------------------------
// 审核（仅 admin）
// ---------------------------------------------------------------------------

const reviewVisible = ref(false)
const reviewTarget = ref<AiScheduledTask | null>(null)
const reviewDecision = ref<AiScheduledReviewDecision>('approved')
const reviewNote = ref('')
const reviewing = ref(false)

function openReview(row: AiScheduledTask) {
  reviewTarget.value = row
  reviewDecision.value = 'approved'
  reviewNote.value = ''
  reviewVisible.value = true
}

async function submitReview() {
  const target = reviewTarget.value
  if (!target || reviewing.value) {
    return
  }
  reviewing.value = true
  try {
    const updated = await reviewAiScheduledTask(target.oid, reviewDecision.value, reviewNote.value)
    replaceRow(updated)
    reviewVisible.value = false
    ElMessage.success(t('ai.schedule.reviewNotice'))
  } catch (error) {
    reportError(error)
  } finally {
    reviewing.value = false
  }
}

// ---------------------------------------------------------------------------
// 执行历史
// ---------------------------------------------------------------------------

const runsVisible = ref(false)
const runsLoading = ref(false)
const runsFailed = ref(false)
const runs = ref<AiScheduledTaskRun[]>([])
const runsTotal = ref(0)
const runsPage = ref(1)
const runsPerpage = ref(20)
const runsTarget = ref<AiScheduledTask | null>(null)

async function loadRuns() {
  const target = runsTarget.value
  if (!target) {
    return
  }
  runsLoading.value = true
  runsFailed.value = false
  try {
    const result = await listAiScheduledTaskRuns(target.oid, runsPage.value, runsPerpage.value)
    runs.value = result.rows
    runsTotal.value = result.total
  } catch (error) {
    runs.value = []
    runsTotal.value = 0
    runsFailed.value = true
    reportError(error)
  } finally {
    runsLoading.value = false
  }
}

function openRuns(row: AiScheduledTask) {
  runsTarget.value = row
  runsPage.value = 1
  runsVisible.value = true
  void loadRuns()
}

function onRunsPageChange(nextPage: number, nextSize: number) {
  runsPage.value = nextPage
  runsPerpage.value = nextSize
  void loadRuns()
}

function runStatusLabel(run: AiScheduledTaskRun): string {
  const map: Record<string, string> = {
    queued: 'ai.schedule.runs.statusQueued',
    running: 'ai.schedule.runs.statusRunning',
    succeeded: 'ai.schedule.runs.statusSucceeded',
    failed: 'ai.schedule.runs.statusFailed',
    cancelled: 'ai.schedule.runs.statusCancelled',
    interrupted: 'ai.schedule.runs.statusInterrupted'
  }
  const key = map[run.status]
  return key ? t(key) : run.status
}

function runStatusType(run: AiScheduledTaskRun): TagType {
  if (run.status === 'succeeded') {
    return 'success'
  }
  if (run.status === 'failed') {
    return 'danger'
  }
  if (run.status === 'queued' || run.status === 'running') {
    return 'primary'
  }
  return 'info'
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 说明卡：审核 + 凭据 + 额度三件事必须先讲清楚 -->
    <section class="yg-alert yg-alert--info ai-schedule__notice">
      <ElIcon class="yg-alert__icon"><InfoFilled /></ElIcon>
      <span class="yg-alert__title">{{ t('ai.schedule.notice') }}</span>
      <span class="yg-alert__desc">{{ t('ai.schedule.noticeDesc') }}</span>
      <span class="yg-alert__desc">{{ t('ai.schedule.noticeExtra') }}</span>
      <span class="ai-schedule__quota">
        {{ t('ai.schedule.quotaTitle') }}：{{ quotaText }}
      </span>
    </section>

    <!-- 管理员区：按人查看 + 审核状态过滤 -->
    <section v-if="isAdmin" class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.schedule.admin.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.schedule.admin.hint') }}</p>
        </div>
      </header>
      <div class="yg-card__body">
        <div class="grid gap-3 md:grid-cols-2">
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.schedule.admin.owner') }}</span>
            <ZSelectPerson
              :model-value="ownerFilter"
              :filter-start="6"
              full-width
              clearable
              :placeholder="t('ai.schedule.admin.ownerPlaceholder')"
              @update:model-value="onOwnerChange"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.schedule.admin.reviewFilter') }}</span>
            <ElSelect v-model="reviewFilter" class="w-full" @change="onReviewFilterChange">
              <ElOption :label="t('ai.schedule.admin.filterAll')" value="all" />
              <ElOption :label="t('ai.schedule.admin.filterPending')" value="pending" />
              <ElOption :label="t('ai.schedule.admin.filterApproved')" value="approved" />
              <ElOption :label="t('ai.schedule.admin.filterRejected')" value="rejected" />
              <ElOption :label="t('ai.schedule.admin.filterDraft')" value="draft" />
            </ElSelect>
          </div>
        </div>
      </div>
    </section>

    <section class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.schedule.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.schedule.subtitle') }}</p>
        </div>
        <div class="yg-card__extra flex flex-wrap items-center gap-2">
          <ElButton :loading="loading" @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.schedule.refresh') }}
          </ElButton>
          <ElButton v-if="canCreate" type="primary" @click="openCreate">
            <ElIcon class="mr-1"><Plus /></ElIcon>
            {{ t('ai.schedule.create') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <!-- 宽屏：表格 -->
        <ElTable v-if="!narrow" v-loading="loading" :data="rows" row-key="oid" stripe>
          <ElTableColumn :label="t('ai.schedule.columns.name')" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <div class="flex flex-col">
                <span class="yg-text-sm">{{ row.name }}</span>
                <span v-if="isAdmin" class="yg-text-xs yg-text-tertiary">{{ row.owner_oid }}</span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.schedule.columns.reviewStatus')" width="110" align="center">
            <template #default="{ row }">
              <ElTag :type="reviewTagType(row)" size="small">
                {{ reviewStatusLabel(row) }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.schedule.columns.lastStatus')" width="130" align="center">
            <template #default="{ row }">
              <ElTooltip :content="lastStatusHint(row)" placement="top" :disabled="!row.last_status">
                <ElTag :type="lastTagType(row)" size="small">
                  {{ lastStatusLabel(row) }}
                </ElTag>
              </ElTooltip>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.schedule.columns.plan')" min-width="150">
            <template #default="{ row }">
              <div class="flex flex-col">
                <span class="yg-text-sm">{{ planLabel(row) }}</span>
                <span class="yg-text-xs yg-text-tertiary">
                  {{ t('ai.schedule.columns.nextRun') }}：{{ formatDateTime(row.next_run_at) }}
                </span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('ai.schedule.columns.runCount')"
            width="100"
            align="right"
          >
            <template #default="{ row }">
              <span class="tabular-nums">{{ row.run_count }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.schedule.columns.enabled')" width="90" align="center">
            <template #default="{ row }">
              <ElSwitch
                :model-value="row.enabled"
                :loading="togglingId === row.oid"
                :disabled="Boolean(togglingId)"
                @change="toggleEnabled(row)"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('ai.schedule.columns.actions')"
            min-width="260"
            fixed="right"
            align="right"
          >
            <template #default="{ row }">
              <div class="flex flex-wrap items-center justify-end gap-1">
                <ElButton text type="primary" size="small" @click="openEdit(row)">
                  <ElIcon class="mr-1"><Edit /></ElIcon>
                  {{ t('ai.common.edit') }}
                </ElButton>
                <ElTooltip
                  :disabled="row.review_status === 'approved'"
                  :content="t('ai.schedule.runNowHint')"
                  placement="top"
                >
                  <span>
                    <ElButton
                      text
                      type="primary"
                      size="small"
                      :loading="runningId === row.oid"
                      :disabled="row.review_status !== 'approved'"
                      @click="runNow(row)"
                    >
                      <ElIcon class="mr-1"><VideoPlay /></ElIcon>
                      {{ t('ai.schedule.runNow') }}
                    </ElButton>
                  </span>
                </ElTooltip>
                <ElButton text type="primary" size="small" @click="openRuns(row)">
                  {{ t('ai.schedule.history') }}
                </ElButton>
                <ElButton
                  v-if="isAdmin"
                  text
                  type="warning"
                  size="small"
                  @click="openReview(row)"
                >
                  {{ t('ai.schedule.reviewAction') }}
                </ElButton>
                <ElButton
                  text
                  type="danger"
                  size="small"
                  :loading="deletingId === row.oid"
                  :disabled="Boolean(deletingId) && deletingId !== row.oid"
                  @click="remove(row)"
                >
                  <ElIcon class="mr-1"><Delete /></ElIcon>
                  {{ t('ai.common.delete') }}
                </ElButton>
              </div>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :description="emptyDescription" :image-size="90">
              <ElButton v-if="loadFailed" @click="load">{{ t('ai.schedule.retry') }}</ElButton>
            </ElEmpty>
          </template>
        </ElTable>

        <!-- 窄屏：卡片 -->
        <div v-else v-loading="loading" class="ai-schedule__cards">
          <template v-if="rows.length === 0">
            <ElEmpty :description="emptyDescription" :image-size="90">
              <ElButton v-if="loadFailed" @click="load">{{ t('ai.schedule.retry') }}</ElButton>
            </ElEmpty>
          </template>
          <article
            v-for="row in rows"
            :key="row.oid"
            class="ai-schedule__card"
          >
            <div class="flex flex-wrap items-start justify-between gap-2">
              <div class="flex flex-col gap-1 min-w-0">
                <span class="yg-text-sm yg-text-primary">{{ row.name }}</span>
                <span v-if="isAdmin" class="yg-text-xs yg-text-tertiary">{{ row.owner_oid }}</span>
              </div>
              <ElSwitch
                :model-value="row.enabled"
                :loading="togglingId === row.oid"
                :disabled="Boolean(togglingId)"
                @change="toggleEnabled(row)"
              />
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <ElTag :type="reviewTagType(row)" size="small">
                {{ reviewStatusLabel(row) }}
              </ElTag>
              <ElTag :type="lastTagType(row)" size="small">
                {{ lastStatusLabel(row) }}
              </ElTag>
              <span class="yg-text-xs yg-text-tertiary">
                {{ t('ai.schedule.columns.runCount') }}：{{ row.run_count }}
              </span>
            </div>
            <div class="flex flex-col gap-1">
              <span class="yg-text-xs">{{ planLabel(row) }}</span>
              <span class="yg-text-xs yg-text-tertiary">
                {{ t('ai.schedule.columns.nextRun') }}：{{ formatDateTime(row.next_run_at) }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-1">
              <ElButton text type="primary" size="small" @click="openEdit(row)">
                {{ t('ai.common.edit') }}
              </ElButton>
              <ElButton
                text
                type="primary"
                size="small"
                :loading="runningId === row.oid"
                :disabled="row.review_status !== 'approved'"
                @click="runNow(row)"
              >
                {{ t('ai.schedule.runNow') }}
              </ElButton>
              <ElButton text type="primary" size="small" @click="openRuns(row)">
                {{ t('ai.schedule.history') }}
              </ElButton>
              <ElButton
                v-if="isAdmin"
                text
                type="warning"
                size="small"
                @click="openReview(row)"
              >
                {{ t('ai.schedule.reviewAction') }}
              </ElButton>
              <ElButton
                text
                type="danger"
                size="small"
                :loading="deletingId === row.oid"
                :disabled="Boolean(deletingId) && deletingId !== row.oid"
                @click="remove(row)"
              >
                {{ t('ai.common.delete') }}
              </ElButton>
            </div>
          </article>
        </div>

        <!-- 管理端视图分页：本页打满 limit 才出现「加载更多」 -->
        <div v-if="hasMore" class="ai-schedule__more">
          <ElButton :loading="loadingMore" :disabled="loading" @click="loadMore">
            {{ t('ai.schedule.loadMore') }}
          </ElButton>
        </div>
      </div>
    </section>

    <!-- 新建 / 编辑 -->
    <ElDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="min(720px, 94vw)"
      top="6vh"
      :close-on-click-modal="false"
      :close-on-press-escape="!submitting"
      :show-close="!submitting"
      append-to-body
    >
      <ElForm
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        class="ai-schedule__form"
      >
        <ElFormItem :label="t('ai.schedule.fields.name')" prop="name">
          <ElInput
            v-model="form.name"
            :placeholder="t('ai.schedule.fields.namePlaceholder')"
            :maxlength="AI_SCHEDULE_NAME_MAX"
            show-word-limit
          />
        </ElFormItem>

        <ElFormItem :label="t('ai.schedule.fields.prompt')" prop="promptText">
          <ElInput
            v-model="form.promptText"
            type="textarea"
            :rows="6"
            :placeholder="t('ai.schedule.fields.promptPlaceholder')"
            :maxlength="AI_SCHEDULE_PROMPT_MAX"
          />
          <p class="ai-schedule__count tabular-nums">{{ promptCount }}</p>
          <p v-if="promptChanged" class="ai-schedule__warn">{{ t('ai.schedule.promptChanged') }}</p>
        </ElFormItem>

        <ElFormItem :label="t('ai.schedule.fields.scheduleKind')">
          <ElRadioGroup v-model="form.scheduleKind">
            <ElRadio value="interval">{{ t('ai.schedule.kind.interval') }}</ElRadio>
            <ElRadio value="cron">{{ t('ai.schedule.kind.cron') }}</ElRadio>
          </ElRadioGroup>
        </ElFormItem>

        <ElFormItem
          v-if="form.scheduleKind === 'interval'"
          :label="t('ai.schedule.fields.interval')"
          prop="intervalMinutes"
        >
          <ElInputNumber
            v-model="form.intervalMinutes"
            :min="AI_SCHEDULE_MIN_INTERVAL_MINUTES"
            :step="15"
            class="w-full"
          />
          <p class="ai-schedule__hint">{{ t('ai.schedule.fields.intervalHint') }}</p>
        </ElFormItem>

        <ElFormItem v-else :label="t('ai.schedule.fields.cron')" prop="cronExpr">
          <ElInput
            v-model="form.cronExpr"
            :placeholder="t('ai.schedule.fields.cronPlaceholder')"
            :maxlength="AI_SCHEDULE_CRON_MAX"
            class="ai-schedule__cron"
          />
          <p class="ai-schedule__hint">{{ t('ai.schedule.fields.cronHint') }}</p>
        </ElFormItem>

        <ElFormItem :label="t('ai.schedule.fields.model')">
          <ElInput
            v-model="form.model"
            :placeholder="t('ai.schedule.fields.modelPlaceholder')"
            clearable
          />
        </ElFormItem>

        <ElFormItem :label="t('ai.schedule.fields.enabled')">
          <ElSwitch v-model="form.enabled" />
        </ElFormItem>

        <p v-if="fieldError.form" class="ai-schedule__error">{{ fieldError.form }}</p>
      </ElForm>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="submitting" @click="dialogVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="submitting" @click="submit">
            {{ t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <!-- 审核（仅 admin） -->
    <ElDialog
      v-model="reviewVisible"
      :title="t('ai.schedule.reviewDialog.title')"
      width="min(520px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!reviewing"
      append-to-body
    >
      <div class="flex flex-col gap-3">
        <p class="yg-text-sm yg-text-tertiary">
          {{ reviewTarget?.name }} · {{ reviewTarget ? planLabel(reviewTarget) : '' }}
        </p>
        <ElRadioGroup v-model="reviewDecision">
          <ElRadio value="approved">{{ t('ai.schedule.reviewDialog.approve') }}</ElRadio>
          <ElRadio value="rejected">{{ t('ai.schedule.reviewDialog.reject') }}</ElRadio>
        </ElRadioGroup>
        <ElInput
          v-model="reviewNote"
          type="textarea"
          :rows="3"
          :placeholder="t('ai.schedule.reviewDialog.notePlaceholder')"
        />
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="reviewing" @click="reviewVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="reviewing" @click="submitReview">
            {{ t('ai.schedule.reviewDialog.submit') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <!-- 执行历史 -->
    <ElDrawer
      v-model="runsVisible"
      direction="rtl"
      :size="narrow ? '100%' : '52%'"
      :title="t('ai.schedule.runs.title')"
    >
      <div v-loading="runsLoading" class="flex flex-col gap-3">
        <template v-if="runsFailed">
          <p class="yg-text-sm yg-text-tertiary">{{ t('ai.schedule.runs.loadFailed') }}</p>
          <ElButton size="small" @click="loadRuns">{{ t('ai.schedule.retry') }}</ElButton>
        </template>
        <template v-else-if="!runsLoading && runs.length === 0">
          <ElEmpty :description="t('ai.schedule.runs.empty')" :image-size="90" />
        </template>
        <article
          v-for="run in runs"
          :key="run.run_id"
          class="ai-schedule__run"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <ElTag :type="runStatusType(run)" size="small">{{ runStatusLabel(run) }}</ElTag>
            <span class="yg-text-xs yg-text-tertiary">{{ run.model || '—' }}</span>
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs">
              {{ t('ai.schedule.runs.startedAt') }}：{{ formatDateTime(run.started_at) }}
            </span>
            <span class="yg-text-xs">
              {{ t('ai.schedule.runs.finishedAt') }}：{{ formatDateTime(run.finished_at) }}
            </span>
          </div>
          <p v-if="run.error_message || run.error_code" class="ai-schedule__run-error">
            {{ t('ai.schedule.runs.error') }}：{{ run.error_message || run.error_code }}
          </p>
        </article>
        <ZPagination
          v-model:current-page="runsPage"
          v-model:page-size="runsPerpage"
          :total="runsTotal"
          @change="onRunsPageChange"
        />
      </div>
    </ElDrawer>
  </div>
</template>

<style scoped>
/* 说明卡：配额提示跟在说明后面，与标题同一行（够宽时）。 */
.ai-schedule__notice {
  position: relative;
}

.ai-schedule__quota {
  flex-basis: 100%;
  padding-left: var(--yg-space-7);
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 表单里的小字说明统一走 tertiary。 */
.ai-schedule__hint {
  margin-top: var(--yg-space-1);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-tertiary);
}

.ai-schedule__count {
  margin-top: var(--yg-space-1);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  text-align: right;
}

.ai-schedule__warn {
  margin-top: var(--yg-space-1);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

.ai-schedule__error {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--el-color-danger);
}

/* cron 表达式用等宽，排错时列位不会跳。 */
.ai-schedule__cron :deep(input) {
  font-family: var(--yg-font-mono);
}

/* 「加载更多」居中，按钮本身不撑满 */
.ai-schedule__more {
  display: flex;
  justify-content: center;
  padding-top: var(--yg-space-3);
}

/* 窄屏卡片列表 */
.ai-schedule__cards {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  min-height: var(--yg-space-10);
}

.ai-schedule__card {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-4);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-lg);
  background: var(--yg-bg-surface);
}

/* 执行历史的一行 */
.ai-schedule__run {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-3);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface);
}

.ai-schedule__run-error {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-danger);
  overflow-wrap: anywhere;
}
</style>

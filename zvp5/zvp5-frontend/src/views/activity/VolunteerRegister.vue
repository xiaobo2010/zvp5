<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElCheckbox } from 'element-plus/es/components/checkbox/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElPageHeader } from 'element-plus/es/components/page-header/index.mjs'
import { ElPopover } from 'element-plus/es/components/popover/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import {
  ArrowLeft,
  Bell,
  CopyDocument,
  Delete,
  Download,
  InfoFilled,
  Plus,
  Refresh,
  RefreshLeft,
  RefreshRight,
  Search,
  UploadFilled,
  View,
  WarningFilled
} from '@element-plus/icons-vue'
import api from '@/api'
import { useHeaderStore } from '@/stores/header'
import { useUserStore } from '@/stores/user'
import type { ActivityMode } from '@/../types'
import type { Activity, ActivityMember, NewActivityMember } from '@/../types/v2'
import { ensureRegisterAccess } from './registerAccess'
import { resolveErrorMessage } from '@/utils/errorMessage'
import {
  loadStudentIndex,
  matchStudentRow,
  normalizePersonName,
  normalizeStudentId,
  suggestStudents,
  syncStudentIndex,
  type StudentIndexState,
  type StudentMatch,
  type StudentRecord
} from '@/utils/studentIndex'
import {
  parseVolunteerSheet,
  recognizeAttachment,
  type OcrVolunteerLine,
  type OcrVolunteerResponse,
  type OcrVolunteerRow,
  type VolunteerOcrProgress,
  type VolunteerOcrRaw
} from '@/api/volunteer'
import { aiErrorCodeOf, aiErrorCopy } from '@/api/ai/errors'
import { listOcrEngines } from '@/api/ai/engines'
import { getBytes } from '@/api/attachment/read'
import { parseOcrText } from '@/utils/ocrText'
import { parseVolunteerXlsx, volunteerRowsToTsv } from '@/utils/volunteerSheet'
import { normalizeOcrBox, type OcrBoxRect } from '@/utils/ocrBox'
import { DEVICE_UNLOCK_REASON, deviceLocked, ensureDeviceUnlock } from '@/plugins/device-unlock'
import { clearTemporaryToken, temporaryToken } from '@/plugins/short-token'

type RegistrationMode = Exclude<ActivityMode, 'hybrid'>
type RowStatus = 'empty' | 'ok' | 'warning' | 'error' | 'saved'
// 两个栏目共用同一张表格组件：新增只写新成员，修改只覆盖已录入成员。
// 两组行各自独立、各自封顶 100，切换栏目时另一组先存起来。
type EditorMode = 'create' | 'edit'

interface RegistrationRow {
  key: string
  studentId: string
  name: string
  classId: string
  className: string
  oid: string
  duration: string
  mode: RegistrationMode
  note: string
  status: RowStatus
  message: string
  candidates: StudentRecord[]
  // 名单内重复：同一 (成员, 模式) 出现多行。用派生标记而不是直接改写 status，
  // 否则重复解除后无法恢复行原本的校验状态。
  duplicate: boolean
  // revalidateRow 算出的原始状态，重复标记只在其上叠加。
  baseStatus: RowStatus
  baseMessage: string
}

// 服务端已存在的成员记录（`GET /v1/activities/{aid}/members` 的一行）。
// 选活动时按页载入成内存索引：表格里敲到已在活动中的成员时能立刻标「已录入」，
// 提交时按「设置时长」覆盖而不是再新增一条。不写进 localStorage 草稿，换活动即失效。
interface ExistingMemberRecord {
  sessionId: string
  member: string
  mode: RegistrationMode
  duration: number
  studentId: string
  name: string
}

interface ExistingMemberRow extends ActivityMember {
  member_name?: string
  user?: { id?: string; name?: string } | null
}

// 一次操作快照：只存表格数据，状态字段（status/message/candidates）由 revalidate 重算。
interface RowSnapshot {
  key: string
  studentId: string
  name: string
  classId: string
  className: string
  oid: string
  duration: string
  mode: RegistrationMode
  note: string
}

// 一条历史：快照 + 它所属的活动和栏目（撤回时可能要先把活动和栏目切回去）。
interface HistoryEntry {
  activityId: string
  mode: EditorMode
  rows: RowSnapshot[]
}

// 覆盖确认弹窗里的一行：姓名 / 学号 / 原时长 / 新时长 / 动作。
interface OverwritePlanRow {
  key: string
  name: string
  studentId: string
  oldDuration: number
  newDuration: number
  changed: boolean
}

// OCR 确认弹窗里的一行：原始文字 + 归一化后的框（没有坐标就是 null）。
interface OcrReviewLine {
  key: number
  text: string
  confidence: number | null
  box: OcrBoxRect | null
  selected: boolean
}

// 顶部可收起提示的一条。
interface RegisterNotice {
  key: 'list' | 'type' | 'ocr'
  type: 'warning' | 'info'
  title: string
  description: string
}

// 草稿按活动隔离：键 = 前缀 + 活动 id，未选活动时用哨兵值。
const DRAFT_KEY_PREFIX = 'volunteer-register-draft-v2:'
const DRAFT_KEY_NONE = `${DRAFT_KEY_PREFIX}__none__`
// 记录上次停留的活动，页面重开时才知道该读哪份草稿。
const DRAFT_LAST_ACTIVITY_KEY = `${DRAFT_KEY_PREFIX}last-activity`
// 旧版单活动草稿：只在升级后的第一次启动里迁移一次，随后删除。
const DRAFT_KEY_LEGACY = 'volunteer-register-draft-v1'
// 顶部三条提示的「不再提示」记录。
const NOTICE_DISMISSED_KEY = 'volunteer-register-notice-dismissed'
const INDEX_STALE_MS = 24 * 60 * 60 * 1000
const MODES: RegistrationMode[] = ['on-campus', 'off-campus', 'social-practice']
// v3 成员列表 size 上限 100。选活动时按页载入已录入成员索引，
// 一次最多回溯 10 页（1000 人）；超出部分标记截断，提交命中时再确认覆盖。
const EXISTING_PAGE_SIZE = 100
const EXISTING_MEMBER_LIMIT = 1000
// 每一栏的行数上限（新增、修改各 100）。粘贴/OCR/新增行都走这个闸门，
// 否则一次全量粘贴就会把每键重算和表格渲染拖到几万行的量级。
const MAX_REGISTER_ROWS = 100
// 单次提交人数上限：超过就拦下，让管理员分批提交，避免一次事务写入过大。
const MAX_SUBMIT_ROWS = 100
// 批量提交相关的请求超时（毫秒）：一次提交要串行/并发跑几十个写请求，
// axios 实例默认的 12s 会在大活动上被中途掐断。
const BATCH_TIMEOUT = 60000
// 逐行 PUT 覆盖：同时最多 8 个在飞，避免一次点提交打爆后端。
const UPDATE_CONCURRENCY = 8
const DURATION_EPSILON = 1e-6
// 撤回/重做：历史栈深度，以及连续输入合并成一次快照的防抖时长。
const HISTORY_LIMIT = 50
const CELL_HISTORY_DELAY = 600

const router = useRouter()
const route = useRoute()
const headerStore = useHeaderStore()
const userStore = useUserStore()
const { t } = useI18n()

const indexState = ref<StudentIndexState>({
  students: [],
  updatedAt: 0,
  source: 'empty'
})
const indexLoading = ref(false)
const indexBooting = ref(false)
const indexProgress = ref('')
const students = computed(() => indexState.value.students)

const activityOptions = ref<Activity[]>([])
const activityLoading = ref(false)
const selectedActivityId = ref('')
const selectedActivity = ref<Activity | null>(null)

// 当前显示的那一栏的行；另一栏的行存在 stashedRows 里，切换时换回来。
const rows = ref<RegistrationRow[]>([])
// 首帧就按 URL 定栏目：避免 /edit 深链先以「添加」标题闪一下。
const editorMode = ref<EditorMode>(modeFromRoute())
const stashedRows: Record<EditorMode, RegistrationRow[]> = { create: [], edit: [] }

// 标题跟随 URL 模式：同一个编辑器承载「添加义工 / 修改义工」两件事，
// 顶栏与浏览器标题必须和当前 URL 一致。
const pageTitle = computed(() =>
  t(editorMode.value === 'edit' ? 'nav.registerEntry.edit.title' : 'nav.registerEntry.create.title')
)
watch(pageTitle, (value) => headerStore.setHeader(value), { immediate: true })
// 「修改已有」栏的服务器搜索：按姓名/学号查当前活动的成员，点「加入表格」进明细。
const editSearchQuery = ref('')
const editSearchLoading = ref(false)
const editSearchResults = ref<ExistingMemberRow[]>([])
const editSearchDone = ref(false)
// 搜索结果被 200 条上限截断（两页都拉满）：提示用户收窄关键词。
const editSearchTruncated = ref(false)
let editSearchSeq = 0
const activeCell = ref<{ r: number; c: number } | null>(null)
const submitLoading = ref(false)
const ocrLoading = ref(false)
const ocrStage = ref('')
const savedActivityId = ref('')
const savedCount = ref(0)
const existingMembers = ref(new Map<string, ExistingMemberRecord>())
const existingLoading = ref(false)
let ocrVerificationToken = ''
// 索引拉取失败：不阻断提交（插入仍带 reject_existing，命中时后端 409 会触发二次确认），
// 只把状态显示出来并给出重试入口。
const existingFetchFailed = ref(false)
// 索引被上限截断：允许提交，但覆盖确认弹窗里要说明可能还有没列出来的命中。
const existingTruncated = ref(false)
// 已录入成员索引的请求序号：换活动后回来晚的旧响应不许覆盖新活动的索引。
let existingLoadSeq = 0
// 覆盖确认弹窗。
const overwriteDialogVisible = ref(false)
const overwriteRows = ref<OverwritePlanRow[]>([])
// OCR 确认弹窗：图片框选 + 文字校对 + 换引擎重识别。
const ocrEngine = ref('')
const ocrEngineOptions = ref<Array<{ value: string; label: string }>>([])
const ocrReviewVisible = ref(false)
const ocrReviewLines = ref<OcrReviewLine[]>([])
const ocrReviewImageUrl = ref('')
const ocrReviewImageSize = ref({ width: 0, height: 0 })
const ocrReviewImageFailed = ref(false)
const ocrReviewAttachmentOid = ref('')
const ocrReviewEngine = ref('')
const ocrReviewConfidence = ref<number | undefined>(undefined)
const ocrReviewWarnings = ref<string[]>([])
const ocrReviewLoading = ref(false)
let ocrLineSeq = 0
let ocrImageObjectUrl = ''
// 顶部提示：默认收起成一个图标按钮，勾选「不再提示」后写进 localStorage。
const noticeVisible = ref(false)
const dismissedNotices = ref<string[]>([])

let rowSeq = 0
let draftTimer: ReturnType<typeof setTimeout> | undefined
let draftIdleHandle: number | undefined
let draftIdleTimer: ReturnType<typeof setTimeout> | undefined
let draftReady = false
// 草稿先于学生名单和活动成员索引恢复；索引齐备前不重算/覆盖草稿身份字段。
let registrationContextReady = false
let savedOids = new Set<string>()
// 当前表格内容归属的活动：换活动时先把它写回旧活动的草稿键，再切到新活动。
let draftOwnerId = ''
// 撤回/重做历史栈。存整表快照，恢复时重算派生状态。
const undoStack = ref<HistoryEntry[]>([])
const redoStack = ref<HistoryEntry[]>([])
// 连续输入的防抖快照：首次击键时抓一份「改之前」的状态，600ms 后并入历史。
let cellHistoryTimer: ReturnType<typeof setTimeout> | undefined
let cellHistorySnapshot: { mode: EditorMode; rows: RowSnapshot[] } | null = null
// 覆盖确认弹窗的 Promise 回调。
let overwriteResolver: ((confirmed: boolean) => void) | null = null

const modeOptions = computed(() => {
  const type = selectedActivity.value?.type
  if (!type || type === 'hybrid') {
    return MODES.map((mode) => ({ value: mode, label: modeLabel(mode) }))
  }
  return [{ value: type, label: modeLabel(type) }]
})

const indexLabel = computed(() => {
  if (indexLoading.value) {
    return indexProgress.value ? `同步中 ${indexProgress.value}` : '同步中…'
  }
  if (indexBooting.value) return '读取本地名单…'
  if (!students.value.length) return '本地名单未就绪'
  if (indexState.value.source === 'index.db') return '本地 index.db'
  if (indexState.value.source === 'network') return '在线同步'
  return '内存名单'
})

const indexTagType = computed<'success' | 'info' | 'warning'>(() => {
  if (!students.value.length && !indexBooting.value && !indexLoading.value) return 'warning'
  if (indexState.value.source === 'network') return 'success'
  return 'info'
})

const indexUpdatedLabel = computed(() => {
  if (!indexState.value.updatedAt) return ''
  const date = new Date(indexState.value.updatedAt)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
})

const nonEmptyRows = computed(() => rows.value.filter(hasContent))
const pendingRows = computed(() => nonEmptyRows.value.filter((row) => row.status !== 'saved'))

const stats = computed(() => {
  let ready = 0
  let warning = 0
  let error = 0
  let saved = 0
  let existing = 0
  let duplicate = 0
  for (const row of nonEmptyRows.value) {
    if (existingOf(row)) existing += 1
    if (row.duplicate) duplicate += 1
    if (row.status === 'ok') ready += 1
    else if (row.status === 'saved') saved += 1
    else if (row.status === 'error') error += 1
    // 名单内重复单独计数：提交前会弹窗确认并用靠后一行覆盖，不算阻断性警告。
    else if (!row.duplicate) warning += 1
  }
  return { total: nonEmptyRows.value.length, ready, warning, error, saved, existing, duplicate }
})

const canSubmit = computed(
  () =>
    Boolean(selectedActivityId.value) &&
    // 已有成员索引没就绪就提交，会把「更新」误判成「新增」（v3 累加，时长翻倍）。
    !existingLoading.value &&
    pendingRows.value.length > 0 &&
    stats.value.warning === 0 &&
    stats.value.error === 0
)

const canUndo = computed(() => undoStack.value.length > 0)
const canRedo = computed(() => redoStack.value.length > 0)

const ocrReviewHasBox = computed(() => ocrReviewLines.value.some((line) => line.box))
const ocrReviewSelectedCount = computed(
  () => ocrReviewLines.value.filter((line) => line.selected).length
)
// 勾选的行按原顺序拼回纯文本，再走一遍和手动粘贴完全相同的解析器：
// 弹窗里看到的预览就是「填入后表格会出现什么」。
const ocrReviewText = computed(() =>
  ocrReviewLines.value
    .filter((line) => line.selected)
    .map((line) => line.text)
    .join('\n')
)
const ocrReviewRows = computed(() => parseOcrText(ocrReviewText.value))
const ocrReviewOverLimit = computed(() => ocrReviewRows.value.length > MAX_REGISTER_ROWS)
const ocrReviewCanApply = computed(
  () => ocrReviewRows.value.length > 0 && !ocrReviewOverLimit.value
)
const ocrReviewEngineLabel = computed(() => {
  if (!ocrReviewEngine.value) return ''
  return (
    ocrEngineOptions.value.find((option) => option.value === ocrReviewEngine.value)?.label ??
    ocrReviewEngine.value
  )
})

// 名单没就绪时常驻的紧凑提示：没名单就会录错人，这条不能被收起来。
const listNoticeActive = computed(
  () => !indexBooting.value && !indexLoading.value && !students.value.length
)

// 顶部可收起的提示：命中「当前适用 且 用户没有勾掉不再提示」才展示。
const registerNotices = computed<RegisterNotice[]>(() => {
  const items: RegisterNotice[] = []
  if (listNoticeActive.value) {
    items.push({
      key: 'list',
      type: 'warning',
      title: t('activity.register.noticeListTitle'),
      description: t('activity.register.noticeListDesc')
    })
  }
  const type = selectedActivity.value?.type
  if (selectedActivity.value && type && type !== 'hybrid') {
    items.push({
      key: 'type',
      type: 'info',
      title: t('activity.register.noticeTypeTitle', { type: modeLabel(type) }),
      description: t('activity.register.noticeTypeDesc')
    })
  }
  items.push({
    key: 'ocr',
    type: 'info',
    title: t('activity.register.noticeOcrTitle'),
    description: t('activity.register.noticeOcrDesc')
  })
  return items.filter((item) => !dismissedNotices.value.includes(item.key))
})

// 三条全被勾掉之后图标按钮直接消失。
const noticeButtonVisible = computed(() => registerNotices.value.length > 0)
const noticeButtonType = computed<'warning' | 'info'>(() =>
  registerNotices.value.some((item) => item.type === 'warning') ? 'warning' : 'info'
)

function existingKey(memberOid: string, mode: string): string {
  return `${memberOid}:${mode}`
}

// 已提交记录的身份键（memberOid:mode）。旧草稿存的是裸 oid，一并兼容。
function savedHas(memberOid: string, mode: string): boolean {
  if (!memberOid) return false
  return savedOids.has(existingKey(memberOid, mode)) || savedOids.has(memberOid)
}

function existingOf(row: RegistrationRow): ExistingMemberRecord | undefined {
  if (!row.oid) return undefined
  return existingMembers.value.get(existingKey(row.oid, row.mode))
}

// 成员 → 已登记模式。旧实现每行每次校验都全量拷贝一份 existingMembers 再 find，
// 行数 × 索引人数变成平方级开销；这里从索引派生一次，查询降到 O(1)。
const existingModesByMember = computed(() => {
  const map = new Map<string, Set<RegistrationMode>>()
  for (const record of existingMembers.value.values()) {
    const modes = map.get(record.member)
    if (modes) modes.add(record.mode)
    else map.set(record.member, new Set([record.mode]))
  }
  return map
})

function existingInOtherMode(row: RegistrationRow): ExistingMemberRecord | undefined {
  if (!row.oid) return undefined
  const modes = existingModesByMember.value.get(row.oid)
  if (!modes) return undefined
  for (const mode of modes) {
    if (mode === row.mode) continue
    const record = existingMembers.value.get(existingKey(row.oid, mode))
    if (record) return record
  }
  return undefined
}

function existingDurationChanged(row: RegistrationRow, record: ExistingMemberRecord): boolean {
  const value = durationValue(row)
  if (value === null) return true
  return Math.abs(value - record.duration) > DURATION_EPSILON
}

// 徽标文案：命中已有成员时显示「现有时长」，时长改过则说明会覆盖。
function existingHint(row: RegistrationRow): string {
  const record = existingOf(row)
  if (!record || row.status === 'saved') return ''
  if (!existingDurationChanged(row, record)) {
    return t('activity.register.existingHintUnchanged')
  }
  return t('activity.register.existingHint', { duration: record.duration })
}

// 同一成员在 hybrid 活动的另一种模式已有记录：成员唯一键含模式，后端允许两种模式并存，
// 所以只做行内提示，不降级行状态、不挡提交。
function otherModeHint(row: RegistrationRow): string {
  if (!row.oid || row.status === 'saved' || existingOf(row)) return ''
  return existingInOtherMode(row) ? t('activity.register.otherModeRecorded') : ''
}

function isRecordableStatus(status: Activity['status'] | undefined): boolean {
  // 后端加成员只接受 pending / effective（activities_v3_router.py），前端保持同口径：
  // 审核中的活动先录入、通过后计入统计是安全的缓冲带；驳回/草稿直接拦下。
  return status === 'effective' || status === 'pending'
}

function modeLabel(mode: ActivityMode | RegistrationMode | string): string {
  return t(`activity.mode.${mode}.name`, mode)
}

function activityStatusLabel(status: Activity['status']): string {
  return t(`activity.status.${status}`, status)
}

function activityStatusType(status: Activity['status']): 'success' | 'warning' | 'info' {
  if (status === 'effective') return 'success'
  if (status === 'pending') return 'warning'
  return 'info'
}

function activityLabel(activity: Activity): string {
  const date = String(activity.date || '').slice(0, 10)
  return [activity.name || '未命名活动', date, activityStatusLabel(activity.status)]
    .filter(Boolean)
    .join(' · ')
}

function defaultMode(): RegistrationMode {
  const type = selectedActivity.value?.type
  return type && type !== 'hybrid' ? type : 'on-campus'
}

function createRow(partial: Partial<RegistrationRow> = {}): RegistrationRow {
  return {
    key: `row-${++rowSeq}`,
    studentId: '',
    name: '',
    classId: '',
    className: '',
    oid: '',
    duration: '',
    mode: defaultMode(),
    note: '',
    status: 'empty',
    message: '',
    candidates: [],
    duplicate: false,
    baseStatus: 'empty',
    baseMessage: '',
    ...partial
  }
}

function resetRows(count = 8) {
  rows.value = Array.from({ length: count }, () => createRow())
  rebuildDuplicateIndex()
}

function rowLimitWarning(dropped?: number): void {
  ElMessage.warning(
    dropped === undefined
      ? t('activity.register.rowLimitReached', { limit: MAX_REGISTER_ROWS })
      : t('activity.register.rowLimitTruncated', { limit: MAX_REGISTER_ROWS, dropped })
  )
}

function ensureRows(count: number) {
  const target = Math.min(count, MAX_REGISTER_ROWS)
  while (rows.value.length < target) rows.value.push(createRow())
  if (count > MAX_REGISTER_ROWS) rowLimitWarning(count - MAX_REGISTER_ROWS)
}

function hasContent(row: RegistrationRow): boolean {
  return Boolean(
    row.studentId.trim() || row.name.trim() || row.duration.trim() || row.note.trim() || row.oid
  )
}

function cleanDuration(raw: string): string {
  const cleaned = raw.replace(/[^\d.]/g, '')
  const firstDot = cleaned.indexOf('.')
  if (firstDot < 0) return cleaned
  return `${cleaned.slice(0, firstDot + 1)}${cleaned.slice(firstDot + 1).replace(/\./g, '')}`
}

function durationValue(row: RegistrationRow): number | null {
  const raw = row.duration.trim()
  if (!raw) return null
  const value = Number(raw)
  if (!Number.isFinite(value) || value <= 0 || value > 25) return null
  return value
}

function durationMessage(row: RegistrationRow): string {
  if (!row.duration.trim()) return t('activity.register.durationRequired')
  if (durationValue(row) === null) return t('activity.register.durationOutOfRange')
  return ''
}

function normalizeMode(raw: unknown): RegistrationMode | null {
  const value = String(raw ?? '')
    .trim()
    .toLowerCase()
  if (!value) return null
  if (value.includes('校内') || value === 'on-campus' || value === 'oncampus') return 'on-campus'
  if (value.includes('校外') || value === 'off-campus' || value === 'offcampus') return 'off-campus'
  if (value.includes('社会实践') || value === 'social-practice' || value === 'socialpractice') {
    return 'social-practice'
  }
  return null
}

// ---------------------------------------------------------------------------
// 学生名单索引
//
// revalidateRow 每次击键都会跑一遍 matchStudentRow，而它内部是对学生名单的线性
// 扫描（最坏 3 万条）。这里在名单换版时一次性建两张精确键索引（学号唯一、
// 姓名键可多条），把「精确查找」降成 O(1)。
//
// 匹配语义仍由 matchStudentRow 决定：索引只用来把它的输入集合缩到 1~2 条，
// 同名多选、前缀/包含候选、mismatch 判定全部原样保留。
// ---------------------------------------------------------------------------
interface StudentLookup {
  byId: Map<string, StudentRecord>
  byName: Map<string, StudentRecord[]>
}

// 与 studentIndex 内部的姓名比较键保持一致：忽略大小写与空格。
function nameKeyOf(raw: string): string {
  return normalizePersonName(raw).toLowerCase().replace(/\s+/g, '')
}

function buildStudentLookup(list: StudentRecord[]): StudentLookup {
  const byId = new Map<string, StudentRecord>()
  const byName = new Map<string, StudentRecord[]>()
  for (const student of list) {
    // 同键保留首条，跟 students.find 的取首个语义对齐。
    if (!byId.has(student.studentId)) byId.set(student.studentId, student)
    const key = nameKeyOf(student.name)
    const group = byName.get(key)
    if (group) group.push(student)
    else byName.set(key, [student])
  }
  return { byId, byName }
}

// 名单引用只在 loadStudentIndex / syncStudentIndex 替换时变，构建成本摊到一次。
const studentLookup = computed(() => buildStudentLookup(students.value))

function matchRow(studentId: string, name: string): StudentMatch {
  const idKey = normalizeStudentId(studentId)
  const personKey = nameKeyOf(name)
  const narrowed: StudentRecord[] = []
  if (idKey) {
    const record = studentLookup.value.byId.get(idKey)
    if (record) narrowed.push(record)
  }
  if (personKey) {
    const sameName = studentLookup.value.byName.get(personKey)
    if (sameName) {
      for (const record of sameName) {
        if (!narrowed.includes(record)) narrowed.push(record)
      }
    }
  }
  return matchStudentRow(narrowed, studentId, name)
}

function revalidateRow(row: RegistrationRow) {
  if (!hasContent(row)) {
    row.baseStatus = 'empty'
    row.baseMessage = ''
    row.candidates = []
    row.oid = ''
    row.classId = ''
    row.className = ''
    applyDuplicateState(row)
    syncDuplicateIndex(row)
    return
  }

  const match = matchRow(row.studentId, row.name)
  row.candidates = match.candidates

  // 非 hybrid 活动的模式先锁定，再按最终模式判断已有记录；否则用户从旧活动草稿切入时，
  // 会先按旧模式查索引，随后才改成活动模式，导致已有成员被误判成新增。
  const activityType = selectedActivity.value?.type
  if (activityType && activityType !== 'hybrid') {
    row.mode = activityType as RegistrationMode
  }

  if (match.record) {
    if (!row.studentId.trim()) row.studentId = match.record.studentId
    if (!row.name.trim()) row.name = match.record.name
    row.classId = match.record.classId
    row.className = match.record.className
    row.oid = match.record.oid
  } else {
    // 本地名单匹配不上时，只有现有 oid 仍对应本活动的已录入记录（毕业/转学）才保留，
    // 否则清除，避免把上一行的身份带进这一行。
    const keepRecorded = Boolean(
      row.oid && existingMembers.value.has(existingKey(row.oid, row.mode))
    )
    if (!keepRecorded) row.oid = ''
    row.classId = ''
    row.className = ''
  }

  // 先解析出 oid 再判「已录入」：否则新敲进来的学号第一下拿不到徽标。
  const recorded = existingOf(row)

  if (match.status === 'missing' && !row.candidates.length) {
    const field = row.studentId.trim() && !row.name.trim() ? 'id' : 'name'
    const query = field === 'id' ? row.studentId : row.name
    if (query.trim().length >= 1) {
      row.candidates = suggestStudents(students.value, query, field, 6)
    }
  }

  let status: RowStatus = 'ok'
  let message = ''
  if (match.status === 'missing') {
    if (recorded && row.oid) {
      status = 'ok'
    } else {
      status = 'error'
      message = match.message
    }
  } else if (match.status === 'ambiguous') {
    status = 'warning'
    message = match.message
  } else if (match.status === 'mismatch') {
    status = 'warning'
    message = match.message
  } else if (match.status !== 'ok') {
    status = 'warning'
    message = match.message || '请填写学号或姓名'
  }

  const invalidDuration = durationMessage(row)
  if (invalidDuration) {
    status = 'error'
    message = message ? `${message}；${invalidDuration}` : invalidDuration
  }

  // 「修改已有」栏只处理已在活动中的人：匹配到学生但活动里没有记录时，
  // 直接标错并引导去新增栏，避免把修改提交成新增。
  if (editorMode.value === 'edit' && !recorded && status === 'ok') {
    status = 'error'
    message = t('activity.register.notInActivity')
  }

  row.baseStatus = status
  row.baseMessage = message
  applyDuplicateState(row)
  syncDuplicateIndex(row)
}

// ---------------------------------------------------------------------------
// 名单内重复检测
//
// 旧实现每次击键都遍历全表重建一张 (成员, 模式) → 行数组 的 Map：行数上限 500
// 时每敲一个键就要分配 500 次 Map 条目和 500 个数组，GC 压力随行数线性上升。
//
// 现在改成增量索引：
//   duplicateGroups    —— 键 → 命中该键的行数组（只有真的重复才有 ≥2 行）
//   duplicateKeyByRow  —— 行 → 它当前所在的分组键（空串表示不在任何分组里）
// revalidateRow 是唯一会改写 oid / mode 的入口，每次都同步一次索引；
// markDuplicates 只遍历真正重复的分组，没有重复时是零开销。
// ---------------------------------------------------------------------------
const duplicateGroups = new Map<string, RegistrationRow[]>()
const duplicateKeyByRow = new Map<string, string>()

function rowDuplicateKey(row: RegistrationRow): string {
  return row.oid ? existingKey(row.oid, row.mode) : ''
}

function syncDuplicateIndex(row: RegistrationRow) {
  const previous = duplicateKeyByRow.get(row.key) ?? ''
  const next = rowDuplicateKey(row)
  if (previous === next) return
  if (previous) {
    const group = duplicateGroups.get(previous)
    if (group) {
      const index = group.indexOf(row)
      if (index >= 0) group.splice(index, 1)
      if (!group.length) duplicateGroups.delete(previous)
    }
  }
  duplicateKeyByRow.set(row.key, next)
  if (next) {
    const group = duplicateGroups.get(next)
    if (group) group.push(row)
    else duplicateGroups.set(next, [row])
  }
}

// 行被移出表格时要从索引里摘掉，否则残留的行会把分组凑成「重复」。
function dropDuplicateIndex(row: RegistrationRow) {
  const previous = duplicateKeyByRow.get(row.key) ?? ''
  if (previous) {
    const group = duplicateGroups.get(previous)
    if (group) {
      const index = group.indexOf(row)
      if (index >= 0) group.splice(index, 1)
      if (!group.length) duplicateGroups.delete(previous)
    }
  }
  duplicateKeyByRow.delete(row.key)
}

// 整表被替换（重置、读草稿）后重建索引。
function rebuildDuplicateIndex() {
  duplicateGroups.clear()
  duplicateKeyByRow.clear()
  for (const row of rows.value) syncDuplicateIndex(row)
}

// 重复标记是叠加在 baseStatus 上的派生状态：解除重复后能原样还原校验结果，
// 反复击键也不会把「名单内重复」文案越拼越长。
function applyDuplicateState(row: RegistrationRow) {
  if (!row.duplicate) {
    row.status = row.baseStatus
    row.message = row.baseMessage
    return
  }
  const reason = t('activity.register.duplicateInTable')
  row.message = row.baseMessage ? `${row.baseMessage}；${reason}` : reason
  // 本身就有错误的行保持错误（先改错再提交），其余降级成「待确认」。
  row.status = row.baseStatus === 'error' ? 'error' : 'warning'
}

function markDuplicates() {
  // 按 (成员, 模式) 三元组去重：同一个人在同一活动里可以有不同模式的记录（hybrid）。
  const duplicated = new Set<string>()
  for (const group of duplicateGroups.values()) {
    if (group.length < 2) continue
    for (const row of group) duplicated.add(row.key)
  }
  for (const row of rows.value) {
    const isDuplicate = duplicated.has(row.key)
    if (row.duplicate === isDuplicate) continue
    row.duplicate = isDuplicate
    applyDuplicateState(row)
  }
}

function revalidateAll() {
  for (const row of rows.value) revalidateRow(row)
  markDuplicates()
}

// ---------------------------------------------------------------------------
// 撤回 / 重做
//
// 历史栈只存表格数据快照；status / message / candidates 这些派生字段在恢复时
// 统一重算，保证「撤回后看到的状态」和「当时的状态」一致。
// 结构性操作（粘贴、OCR、增删行、复制行、填充下行、清空、换活动）立刻入栈；
// 单元格连续输入用 600ms 防抖合并成一条历史，避免每敲一个字就多一步。
//
// 每条历史带着它所属的活动：撤回时如果和当前活动不是同一个，就先切回去，
// 免得把 A 活动的行落到 B 活动的表里。
// ---------------------------------------------------------------------------
function snapshotRows(): RowSnapshot[] {
  return rows.value.map((row) => ({
    key: row.key,
    studentId: row.studentId,
    name: row.name,
    classId: row.classId,
    className: row.className,
    oid: row.oid,
    duration: row.duration,
    mode: row.mode,
    note: row.note
  }))
}

function restoreSnapshot(snapshot: RowSnapshot[]) {
  // 撤回也不会把已经写进服务端的人重新摆回登记表：这条页面只展示未提交内容。
  rows.value = snapshot
    .filter((item) => !savedHas(item.oid, item.mode))
    .map((item) => createRow({ ...item }))
  if (!rows.value.length) resetRows(8)
  rebuildDuplicateIndex()
  if (registrationContextReady) revalidateAll()
  applySavedState()
}

// 撤回只恢复表格数据：服务端已经写进去的行仍然是「已提交」，按 savedOids 重新推导，
// 不能因为撤回一次就把它们退回成待提交（那会重复写入）。
function applySavedState() {
  if (!savedOids.size) return
  if (savedActivityId.value && savedActivityId.value !== selectedActivityId.value) return
  for (const row of rows.value) {
    if (savedHas(row.oid, row.mode)) {
      row.status = 'saved'
      row.message = t('activity.register.savedRow')
    }
  }
}

function pushUndoStack(entry: HistoryEntry) {
  undoStack.value = [...undoStack.value, entry].slice(-HISTORY_LIMIT)
}

// 连续输入：先抓一份「改之前」的快照，600ms 内的后续击键并入同一条历史。
function beginCellHistory() {
  if (!cellHistorySnapshot) {
    cellHistorySnapshot = { mode: editorMode.value, rows: snapshotRows() }
  }
  if (cellHistoryTimer) clearTimeout(cellHistoryTimer)
  cellHistoryTimer = setTimeout(() => {
    cellHistoryTimer = undefined
    if (!cellHistorySnapshot) return
    pushUndoStack({
      activityId: draftOwnerId,
      mode: cellHistorySnapshot.mode,
      rows: cellHistorySnapshot.rows
    })
    cellHistorySnapshot = null
    redoStack.value = []
  }, CELL_HISTORY_DELAY)
}

// 立刻把待合并的连续输入并入历史（结构性操作前、撤回前都要先调用）。
function commitCellHistory() {
  if (cellHistoryTimer) {
    clearTimeout(cellHistoryTimer)
    cellHistoryTimer = undefined
  }
  if (!cellHistorySnapshot) return
  pushUndoStack({
    activityId: draftOwnerId,
    mode: cellHistorySnapshot.mode,
    rows: cellHistorySnapshot.rows
  })
  cellHistorySnapshot = null
}

// 结构性操作：立刻入栈。
function recordHistory() {
  commitCellHistory()
  pushUndoStack({ activityId: draftOwnerId, mode: editorMode.value, rows: snapshotRows() })
  redoStack.value = []
}

async function undo() {
  commitCellHistory()
  if (!undoStack.value.length) return
  const entry = undoStack.value[undoStack.value.length - 1]
  undoStack.value = undoStack.value.slice(0, -1)
  redoStack.value = [
    ...redoStack.value,
    { activityId: draftOwnerId, mode: editorMode.value, rows: snapshotRows() }
  ]
  if (entry.activityId !== draftOwnerId) {
    await switchActivity(entry.activityId, false)
  }
  if (entry.mode !== editorMode.value) switchEditorMode(entry.mode)
  restoreSnapshot(entry.rows)
}

async function redo() {
  if (!redoStack.value.length) return
  const entry = redoStack.value[redoStack.value.length - 1]
  redoStack.value = redoStack.value.slice(0, -1)
  undoStack.value = [
    ...undoStack.value,
    { activityId: draftOwnerId, mode: editorMode.value, rows: snapshotRows() }
  ]
  if (entry.activityId !== draftOwnerId) {
    await switchActivity(entry.activityId, false)
  }
  if (entry.mode !== editorMode.value) switchEditorMode(entry.mode)
  restoreSnapshot(entry.rows)
}

// Ctrl/Cmd+Z 撤回，Ctrl/Cmd+Shift+Z 重做。表格单元格以外的输入框交给浏览器原生撤销。
function onGlobalKeydown(event: KeyboardEvent) {
  if (!event.ctrlKey && !event.metaKey) return
  if (event.altKey) return
  const key = event.key.toLowerCase()
  if (key !== 'z' && key !== 'y') return
  if (key === 'y' && !event.shiftKey) return
  const target = event.target as HTMLElement | null
  const tag = target?.tagName
  if (
    (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) &&
    !target?.closest('[data-cell]')
  ) {
    return
  }
  event.preventDefault()
  if (key === 'y' || event.shiftKey) void redo()
  else void undo()
}

function onIdInput(row: RegistrationRow, event: Event) {
  beginCellHistory()
  row.studentId = normalizeStudentId((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onNameInput(row: RegistrationRow, event: Event) {
  beginCellHistory()
  row.name = normalizePersonName((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onDurationInput(row: RegistrationRow, event: Event) {
  beginCellHistory()
  row.duration = cleanDuration((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onModeChange(row: RegistrationRow) {
  beginCellHistory()
  revalidateRow(row)
  markDuplicates()
}

function applyCandidate(row: RegistrationRow, candidate: StudentRecord) {
  beginCellHistory()
  row.studentId = candidate.studentId
  row.name = candidate.name
  row.classId = candidate.classId
  row.className = candidate.className
  row.oid = candidate.oid
  row.candidates = []
  revalidateRow(row)
  markDuplicates()
}

function showCandidates(r: number, c: number): boolean {
  const row = rows.value[r]
  const active = activeCell.value
  if (!row || !active || active.r !== r || active.c !== c || !row.candidates.length) {
    return false
  }
  if (row.status === 'ok' || row.status === 'saved') return false
  if (c === 0) return !row.oid
  return row.status === 'warning' || !row.oid
}

function onCellFocus(r: number, c: number) {
  activeCell.value = { r, c }
}

function focusCell(r: number, c: number) {
  if (r < 0 || r >= rows.value.length || c < 0 || c > 4) return
  activeCell.value = { r, c }
  void nextTick(() => {
    const element = document.querySelector<HTMLElement>(`[data-cell="${r}-${c}"]`)
    element?.focus()
    if (element instanceof HTMLInputElement) element.select()
  })
}

function fillDown(r: number, c: number) {
  if (r >= rows.value.length - 1) return
  recordHistory()
  const source = rows.value[r]
  const target = rows.value[r + 1]
  const value = readCell(source, c)
  writeCell(target, c, value)
  revalidateRow(target)
  markDuplicates()
  focusCell(r + 1, c)
}

function readCell(row: RegistrationRow, c: number): string {
  if (c === 0) return row.studentId
  if (c === 1) return row.name
  if (c === 2) return row.duration
  if (c === 3) return row.mode
  return row.note
}

function writeCell(row: RegistrationRow, c: number, raw: string) {
  if (c === 0) row.studentId = normalizeStudentId(raw)
  else if (c === 1) row.name = normalizePersonName(raw)
  else if (c === 2) row.duration = cleanDuration(raw)
  else if (c === 3) row.mode = normalizeMode(raw) ?? defaultMode()
  else row.note = raw
}

function onGridKeydown(event: KeyboardEvent, r: number, c: number) {
  if (event.isComposing) return
  const target = event.target as HTMLElement
  if (target.tagName === 'SELECT' && event.key.startsWith('Arrow')) return
  if (event.shiftKey) return

  if (event.key === 'ArrowRight') {
    event.preventDefault()
    focusCell(r, Math.min(c + 1, 4))
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    focusCell(r, Math.max(c - 1, 0))
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    focusCell(r + 1, c)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    focusCell(r - 1, c)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    if (r >= rows.value.length - 1) addRow()
    focusCell(r + 1, c)
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') {
    event.preventDefault()
    fillDown(r, c)
  }
}

function addRow() {
  if (rows.value.length >= MAX_REGISTER_ROWS) {
    rowLimitWarning()
    return
  }
  recordHistory()
  rows.value.push(createRow())
  void nextTick(() => focusCell(rows.value.length - 1, 0))
}

function duplicateRow(index: number) {
  const source = rows.value[index]
  if (!source) return
  if (rows.value.length >= MAX_REGISTER_ROWS) {
    rowLimitWarning()
    return
  }
  recordHistory()
  rows.value.splice(
    index + 1,
    0,
    createRow({
      studentId: source.studentId,
      name: source.name,
      classId: source.classId,
      className: source.className,
      oid: source.oid,
      duration: source.duration,
      mode: source.mode,
      note: source.note
    })
  )
  revalidateAll()
}

// 删除行：有内容的行要二次确认（危险操作，红色确认按钮）；空行直接删。
async function removeRow(index: number) {
  const row = rows.value[index]
  if (!row) return
  if (hasContent(row)) {
    try {
      await ElMessageBox.confirm(
        t('activity.register.removeRowConfirm', { name: rowLabel(row) }),
        t('activity.register.removeRowTitle'),
        {
          confirmButtonText: t('activity.register.deleteAction'),
          cancelButtonText: t('activity.register.cancelAction'),
          confirmButtonClass: 'el-button--danger',
          type: 'warning'
        }
      )
    } catch {
      return
    }
  }
  recordHistory()
  dropDuplicateIndex(row)
  rows.value.splice(index, 1)
  if (!rows.value.length) resetRows(1)
  revalidateAll()
}

function parseDelimitedLine(line: string, delimiter: string): string[] {
  const cells: string[] = []
  let current = ''
  let quoted = false
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"'
        index += 1
      } else {
        quoted = !quoted
      }
    } else if (char === delimiter && !quoted) {
      cells.push(current)
      current = ''
    } else {
      current += char
    }
  }
  cells.push(current)
  return cells
}

function isNumericCell(value: string): boolean {
  return /^\d/.test(value.trim())
}

function detectMapping(first: string[], startColumn: number): Record<number, number> {
  const normalized = first.map((cell) => cell.trim().toLowerCase().replace(/\s+/g, ''))
  const aliases: Record<string, number> = {
    学号: 0,
    编号: 0,
    id: 0,
    studentid: 0,
    姓名: 1,
    name: 1,
    时长: 2,
    小时: 2,
    duration: 2,
    hours: 2,
    模式: 3,
    mode: 3,
    类型: 3,
    备注: 4,
    note: 4,
    remark: 4
  }
  if (normalized.some((cell) => cell in aliases)) {
    const mapping: Record<number, number> = {}
    normalized.forEach((cell, index) => {
      const column = aliases[cell]
      if (column !== undefined) mapping[column] = index
    })
    return mapping
  }
  if (first.length === 1) return { [startColumn]: 0 }
  if (first.length >= 6 && !isNumericCell(first[2] ?? '') && isNumericCell(first[3] ?? '')) {
    return { 0: 0, 1: 1, 2: 3, 3: 4, 4: 5 }
  }
  return { 0: 0, 1: 1, 2: 2, 3: 3, 4: 4 }
}

function applyText(text: string, start: { r: number; c: number }) {
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
  while (lines.length && lines[lines.length - 1] === '') lines.pop()
  if (!lines.length) return

  const delimiter = lines.some((line) => line.includes('\t'))
    ? '\t'
    : lines.some((line) => line.includes(','))
      ? ','
      : ''
  const matrix = delimiter
    ? lines.map((line) => parseDelimitedLine(line, delimiter))
    : lines.map((line) => [line])

  const header = matrix[0]?.some((cell) =>
    ['学号', '编号', '姓名', '时长', '小时', '模式', '备注', 'duration', 'name', 'id'].includes(
      cell.trim().toLowerCase()
    )
  )
  const mapping = detectMapping(matrix[0] ?? [], start.c)
  const data = header ? matrix.slice(1) : matrix
  // 行数封顶：从 start.r 起最多再用 MAX_REGISTER_ROWS 行，多出的数据行丢弃。
  const room = Math.max(0, MAX_REGISTER_ROWS - start.r)
  const accepted = data.slice(0, room)
  if (!accepted.length) {
    rowLimitWarning(data.length)
    return
  }
  ensureRows(start.r + accepted.length)

  recordHistory()
  accepted.forEach((cells, rowOffset) => {
    const row = rows.value[start.r + rowOffset]
    if (!row) return
    for (const [columnKey, cellIndex] of Object.entries(mapping)) {
      const column = Number(columnKey)
      writeCell(row, column, cells[cellIndex] ?? '')
    }
    revalidateRow(row)
  })
  markDuplicates()
  if (accepted.length < data.length) rowLimitWarning(data.length - accepted.length)
  ElMessage.success(`已填入 ${accepted.length} 行，请复核匹配结果`)
}

function onGridPaste(event: ClipboardEvent) {
  const text = event.clipboardData?.getData('text/plain') ?? ''
  if (!text) return
  if (!text.includes('\n') && !text.includes('\t')) return
  event.preventDefault()
  applyText(text, activeCell.value ?? { r: 0, c: 0 })
}

async function pasteFromClipboard() {
  try {
    if (!navigator.clipboard?.readText) throw new Error('clipboard unavailable')
    const text = await navigator.clipboard.readText()
    if (!text.trim()) {
      ElMessage.info('剪贴板为空')
      return
    }
    applyText(text, { r: 0, c: 0 })
  } catch {
    ElMessage.warning('浏览器未授权读取剪贴板，请在表格内直接粘贴')
  }
}

const importInput = ref<HTMLInputElement | null>(null)
const ocrInput = ref<HTMLInputElement | null>(null)

function triggerImport() {
  void ElMessageBox.confirm(
    '支持 .xlsx、.xls、.csv 和从 Excel 复制粘贴。表格前三列按“学号/姓名/时长”或“姓名/学号/时长”排列，第三列始终按时长读取；有标题行也可以导入。',
    '导入格式提醒',
    {
      confirmButtonText: '选择文件',
      cancelButtonText: '取消',
      type: 'info'
    }
  )
    .then(() => importInput.value?.click())
    .catch(() => undefined)
}

// OCR 属于 AI 操作，必须先用当前账号密码换一张 5 分钟、purpose=verify 的短时
// 凭证；平板还要先解锁媒体库（同一套临时凭证），这里复用避免连弹两次密码框。
async function acquireOcrProof(): Promise<string> {
  const proof = deviceLocked()
    ? await ensureDeviceUnlock({ reason: DEVICE_UNLOCK_REASON })
    : await temporaryToken(String(userStore._id || userStore.id), 'OCR 操作需要验证当前账号密码')
  if (!proof) throw new Error('未取得验证凭证')
  return proof
}

async function triggerOcr() {
  if (!selectedActivityId.value) {
    ElMessage.warning('请先选择活动')
    return
  }
  try {
    ocrVerificationToken = await acquireOcrProof()
    ocrInput.value?.click()
  } catch {
    ocrVerificationToken = ''
    ElMessage({
      message: '未验证密码，已取消本次识别',
      type: 'warning',
      plain: true,
      grouping: true
    })
  }
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const buffer = await file.arrayBuffer()
    if (/\.(xlsx|xls)$/i.test(file.name)) {
      const rows = await parseVolunteerXlsx(buffer)
      if (!rows.length) {
        ElMessage.info('表格中没有可导入的数据')
        return
      }
      applyText(volunteerRowsToTsv(rows), { r: 0, c: 0 })
      return
    }
    const bytes = new Uint8Array(buffer)
    const hasUtf8Bom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf
    const utf8Text = new TextDecoder('utf-8').decode(buffer)
    const text =
      hasUtf8Bom || !utf8Text.includes('\ufffd') ? utf8Text : new TextDecoder('gbk').decode(buffer)
    applyText(text, { r: 0, c: 0 })
  } catch {
    ElMessage.error('读取文件失败')
  } finally {
    input.value = ''
  }
}

function describeOcrProgress(progress: VolunteerOcrProgress): string {
  if (progress.stage === 'uploading') {
    return progress.progress === undefined ? '上传图片…' : `上传图片 ${progress.progress}%`
  }
  if (progress.stage === 'queued') return '已加入 OCR 队列…'
  if (progress.status === 'processing') return 'OCR 识别中…'
  return '等待 OCR 结果…'
}

async function onOcrFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  ocrLoading.value = true
  ocrStage.value = '准备上传…'
  try {
    if (!ocrVerificationToken) throw new Error('请重新验证密码后使用 OCR')
    const verificationToken = ocrVerificationToken
    ocrVerificationToken = ''
    const result = await parseVolunteerSheet(
      file,
      verificationToken,
      (progress) => {
        ocrStage.value = describeOcrProgress(progress)
      },
      ocrEngine.value || undefined
    )
    openOcrReview(result)
  } catch (error) {
    const code = aiErrorCodeOf(error)
    if (code) {
      const copy = aiErrorCopy(code, t)
      ElMessage.error(`${copy.title}：${copy.hint}`)
    } else {
      ElMessage.error(resolveErrorMessage(error, 'activity.register.ocrReviewRerunFailed'))
    }
  } finally {
    ocrLoading.value = false
    ocrStage.value = ''
    input.value = ''
  }
}

// 引擎清单只用于选择：403（无权限）或网络失败时静默隐藏下拉，页面只留「自动」。
async function loadOcrEngines() {
  try {
    const engines = await listOcrEngines()
    const options = new Map<string, string>()
    for (const engine of engines) {
      if (!engine.enabled || options.has(engine.kind)) continue
      options.set(engine.kind, engine.name || engine.kind)
    }
    ocrEngineOptions.value = [...options.entries()].map(([value, label]) => ({ value, label }))
  } catch {
    ocrEngineOptions.value = []
  }
}

function toOcrReviewLines(lines: OcrVolunteerLine[]): OcrReviewLine[] {
  return lines.map((line) => ({
    key: (ocrLineSeq += 1),
    text: line.text,
    confidence: line.confidence ?? null,
    box: normalizeOcrBox(line.box),
    selected: true
  }))
}

function openOcrReview(result: OcrVolunteerResponse) {
  ocrReviewLines.value = toOcrReviewLines(result.lines)
  ocrReviewEngine.value = result.engine ?? ''
  ocrReviewConfidence.value = result.confidence
  ocrReviewWarnings.value = result.warnings ?? []
  ocrReviewAttachmentOid.value = result.attachmentOid ?? ''
  ocrReviewVisible.value = true
  void loadOcrReviewImage(ocrReviewAttachmentOid.value)
}

function releaseOcrImage() {
  if (ocrImageObjectUrl) {
    URL.revokeObjectURL(ocrImageObjectUrl)
    ocrImageObjectUrl = ''
  }
  ocrReviewImageUrl.value = ''
  ocrReviewImageSize.value = { width: 0, height: 0 }
}

async function loadOcrReviewImage(oid: string) {
  releaseOcrImage()
  ocrReviewImageFailed.value = false
  if (!oid) {
    ocrReviewImageFailed.value = true
    return
  }
  try {
    const blob = await getBytes(oid)
    if (ocrReviewAttachmentOid.value !== oid) return
    ocrImageObjectUrl = URL.createObjectURL(blob)
    ocrReviewImageUrl.value = ocrImageObjectUrl
  } catch {
    if (ocrReviewAttachmentOid.value === oid) ocrReviewImageFailed.value = true
  }
}

function onOcrReviewImageLoad(event: Event) {
  const image = event.target as HTMLImageElement
  ocrReviewImageSize.value = { width: image.naturalWidth, height: image.naturalHeight }
}

// 坐标按原图尺寸换算成百分比：图片缩放后框仍然贴在字上。
function ocrBoxStyle(box: OcrBoxRect): Record<string, string> {
  const { width, height } = ocrReviewImageSize.value
  if (!width || !height) return {}
  const left = Math.max(0, Math.min(box.left, width))
  const top = Math.max(0, Math.min(box.top, height))
  const right = Math.min(width, Math.max(box.left + box.width, 0))
  const bottom = Math.min(height, Math.max(box.top + box.height, 0))
  return {
    left: `${(left / width) * 100}%`,
    top: `${(top / height) * 100}%`,
    width: `${(Math.max(0, right - left) / width) * 100}%`,
    height: `${(Math.max(0, bottom - top) / height) * 100}%`
  }
}

function toggleOcrReviewLine(index: number) {
  const line = ocrReviewLines.value[index]
  if (line) line.selected = !line.selected
}

function setAllOcrReviewLines(selected: boolean) {
  for (const line of ocrReviewLines.value) line.selected = selected
}

function closeOcrReview() {
  ocrReviewVisible.value = false
}

function resetOcrReview() {
  ocrReviewLines.value = []
  ocrReviewWarnings.value = []
  ocrReviewConfidence.value = undefined
  ocrReviewEngine.value = ''
  ocrReviewAttachmentOid.value = ''
  ocrReviewImageFailed.value = false
  releaseOcrImage()
}

function ocrVerificationRejected(error: unknown): boolean {
  const status =
    (error as { status?: unknown })?.status ??
    (error as { response?: { status?: unknown } })?.response?.status
  return status === 401 || status === 403
}

// 换引擎重识别：附件 oid 不变，只重新入队 + 轮询。凭证被否时清缓存重取一次，
// 再失败就停手——继续重试只会把用户困在密码框里。
async function rerunOcrReview() {
  const oid = ocrReviewAttachmentOid.value
  if (!oid || ocrReviewLoading.value) return
  ocrReviewLoading.value = true
  ocrStage.value = '准备重新识别…'
  const reportProgress = (progress: VolunteerOcrProgress) => {
    ocrStage.value = describeOcrProgress(progress)
  }
  try {
    let proof = await acquireOcrProof()
    let raw: VolunteerOcrRaw
    try {
      raw = await recognizeAttachment(oid, proof, ocrEngine.value || undefined, reportProgress)
    } catch (error) {
      if (!ocrVerificationRejected(error)) throw error
      clearTemporaryToken(String(userStore._id || userStore.id))
      proof = await acquireOcrProof()
      raw = await recognizeAttachment(oid, proof, ocrEngine.value || undefined, reportProgress)
    }
    ocrReviewLines.value = toOcrReviewLines(raw.lines)
    ocrReviewEngine.value = raw.engine ?? ''
    ocrReviewConfidence.value = raw.confidence
    ocrReviewWarnings.value = []
    if (!raw.lines.length) {
      ElMessage.warning(t('activity.register.ocrReviewNoText'))
    }
  } catch (error) {
    const code = aiErrorCodeOf(error)
    if (code) {
      const copy = aiErrorCopy(code, t)
      ElMessage.error(`${copy.title}：${copy.hint}`)
    } else {
      ElMessage.error(
        t('activity.register.ocrReviewRerunFailed', {
          reason: resolveErrorMessage(error)
        })
      )
    }
  } finally {
    ocrReviewLoading.value = false
    ocrStage.value = ''
  }
}

// 把确认过的 OCR 行铺进表格。抽成独立函数：首次识别和「换引擎重识别」共用一条落表路径。
function applyOcrRows(list: OcrVolunteerRow[], engineLabel?: string): boolean {
  // OCR 识别的是新登记的人：识别完自动切到「新增」栏再铺数据。
  if (editorMode.value !== 'create') switchEditorMode('create')

  // 从最后一条已有内容之后开始，避免中间空行导致 OCR 覆盖后面的人工录入。
  const base = rows.value.reduce((last, row, index) => (hasContent(row) ? index : last), -1) + 1
  // 行数封顶：OCR 一次可能吐出很多行，超出部分直接丢弃并提示。
  const room = Math.max(0, MAX_REGISTER_ROWS - base)
  const accepted = list.slice(0, room)
  if (!accepted.length) {
    rowLimitWarning(list.length)
    return false
  }
  ensureRows(base + accepted.length)
  recordHistory()
  accepted.forEach((item, offset) => {
    const row = rows.value[base + offset]
    if (!row) return
    applyOcrRow(row, item)
    revalidateRow(row)
  })
  markDuplicates()
  if (accepted.length < list.length) rowLimitWarning(list.length - accepted.length)
  const engine = engineLabel ? `（${engineLabel}）` : ''
  ElMessage.success(`OCR 识别 ${accepted.length} 行${engine}，已按本地名单校正`)
  return true
}

function confirmOcrReview() {
  if (!ocrReviewCanApply.value) return
  const applied = applyOcrRows(ocrReviewRows.value, ocrReviewEngineLabel.value)
  if (applied) closeOcrReview()
}

function applyOcrRow(row: RegistrationRow, item: OcrVolunteerRow) {
  row.studentId = normalizeStudentId(String(item.studentId ?? ''))
  row.name = normalizePersonName(String(item.name ?? ''))
  row.duration = item.duration == null ? '' : cleanDuration(String(item.duration))
  row.mode = normalizeMode(item.mode) ?? defaultMode()
  row.note = String(item.note ?? '')
}

async function searchActivities(query: string) {
  activityLoading.value = true
  try {
    const result = await api.activity.read.campus('', 1, 30, query)
    // 审核中的活动也允许录入：后端加成员只接受 pending/effective，时间聚合只看 effective，
    // 数据上安全。只挡 refused/draft（draft 不在 status 枚举内，兜底同口径）。
    const merged = result.activities.filter((activity) => isRecordableStatus(activity.status))
    if (
      selectedActivity.value &&
      isRecordableStatus(selectedActivity.value.status) &&
      !merged.some((activity) => activity._id === selectedActivity.value?._id)
    ) {
      merged.unshift(selectedActivity.value)
    }
    activityOptions.value = merged
  } catch {
    activityOptions.value =
      selectedActivity.value && isRecordableStatus(selectedActivity.value.status)
        ? [selectedActivity.value]
        : []
  } finally {
    activityLoading.value = false
  }
}

async function hydrateSelectedActivity() {
  if (!selectedActivityId.value) {
    selectedActivity.value = null
    return
  }
  const found = activityOptions.value.find((activity) => activity._id === selectedActivityId.value)
  if (found) {
    selectedActivity.value = found
    return
  }
  try {
    const detail = await api.activity.read.single(selectedActivityId.value)
    selectedActivity.value = detail.activity
    activityOptions.value = [detail.activity, ...activityOptions.value]
  } catch {
    const failedActivityId = selectedActivityId.value
    selectedActivityId.value = ''
    selectedActivity.value = null
    if (draftOwnerId === failedActivityId) {
      draftOwnerId = ''
      rows.value = []
      stashedRows.create = []
      stashedRows.edit = []
      resetRows(8)
    }
  }
}

// 已录入成员索引：选活动时按页拉回 (memberOid, mode) → 记录，供表格即时判重。
// 这里只建索引，不把成员填进表格——批量修改和新增走同一张表：管理员直接录入/粘贴
// 已在活动中的人，提交时按「设置时长」覆盖；新面孔才走新增。
async function fetchExistingMemberPage(
  aid: string,
  page: number,
  query = ''
): Promise<{ rows: ExistingMemberRow[]; total: number }> {
  const result = (await api.activity.member.reads_v3(aid, page, EXISTING_PAGE_SIZE, query, {
    timeout: BATCH_TIMEOUT
  })) as unknown as { members?: ExistingMemberRow[]; total?: number }
  return { rows: result.members ?? [], total: Number(result.total) || 0 }
}

async function loadExistingMembers(aid: string): Promise<void> {
  const seq = ++existingLoadSeq
  existingLoading.value = true
  existingFetchFailed.value = false
  existingTruncated.value = false
  const maxPages = EXISTING_MEMBER_LIMIT / EXISTING_PAGE_SIZE
  try {
    // 第 1 页顺带拿到 total，其余页按 total 算出的页数并行取：大活动不再串行等 10 次往返。
    const first = await fetchExistingMemberPage(aid, 1)
    if (seq !== existingLoadSeq) return
    const total = first.total
    if (selectedActivityId.value === aid) savedCount.value = total
    const firstRows = first.rows
    let pageCount = maxPages
    if (total > 0) pageCount = Math.min(Math.ceil(total / EXISTING_PAGE_SIZE), maxPages)
    else if (firstRows.length < EXISTING_PAGE_SIZE) pageCount = 1
    const rest =
      pageCount > 1
        ? await Promise.all(
            Array.from({ length: pageCount - 1 }, (_, index) =>
              fetchExistingMemberPage(aid, index + 2)
            )
          )
        : []
    if (seq !== existingLoadSeq) return
    const next = new Map<string, ExistingMemberRecord>()
    const rows = [...firstRows]
    for (const page of rest) rows.push(...page.rows)
    for (const member of rows) {
      const mode = normalizeMode(member.mode)
      if (!mode || !member.member || !member._id) continue
      next.set(existingKey(String(member.member), mode), {
        sessionId: String(member._id),
        member: String(member.member),
        mode,
        duration: Number(member.duration) || 0,
        studentId: normalizeStudentId(String(member.user?.id ?? '')),
        name: normalizePersonName(String(member.member_name ?? member.user?.name ?? ''))
      })
    }
    existingMembers.value = next
    // 还有下一页但已到回溯上限：标记截断，提交时若命中未列出的人再确认覆盖。
    const fetchedRows = pageCount * EXISTING_PAGE_SIZE
    existingTruncated.value = total > fetchedRows && pageCount >= maxPages
    if (existingTruncated.value) {
      ElMessage.warning(t('activity.register.existingTruncated', { limit: EXISTING_MEMBER_LIMIT }))
    }
  } catch {
    if (seq !== existingLoadSeq) return
    existingMembers.value = new Map()
    existingFetchFailed.value = true
    ElMessage.warning(t('activity.register.existingLoadFailed'))
  } finally {
    if (seq === existingLoadSeq) existingLoading.value = false
  }
  revalidateAll()
}

// 活动选定后的统一收口：拦掉不可录入状态、重建已录入索引、重算行状态。
async function syncActivityContext(preselect = false) {
  const activity = selectedActivity.value
  if (!activity) {
    resetExistingIndex()
    if (registrationContextReady) revalidateAll()
    return
  }
  if (!isRecordableStatus(activity.status)) {
    const blockedActivityId = selectedActivityId.value
    selectedActivityId.value = ''
    selectedActivity.value = null
    resetExistingIndex()
    if (draftOwnerId === blockedActivityId) {
      draftOwnerId = ''
      rows.value = []
      stashedRows.create = []
      stashedRows.edit = []
      resetRows(8)
    }
    ElMessage.warning(t('activity.register.blockedRecordNotice'))
    return
  }
  if (preselect && activity.status === 'pending') {
    ElMessage.info(t('activity.register.pendingRecordNotice'))
  }
  resetExistingIndex()
  await loadExistingMembers(activity._id)
}

// 换活动时把索引和搜索结果全部作废：旧的 member oid / sessionId 绝不能带到新活动里。
function resetExistingIndex() {
  existingLoadSeq += 1
  existingMembers.value = new Map()
  existingLoading.value = false
  existingFetchFailed.value = false
  existingTruncated.value = false
  resetEditSearch()
}

function resetEditSearch() {
  editSearchSeq += 1
  editSearchQuery.value = ''
  editSearchResults.value = []
  editSearchDone.value = false
  editSearchTruncated.value = false
  editSearchLoading.value = false
}

// 「修改已有」栏的服务器搜索：按姓名或学号查当前活动的成员，最多取 200 条。
async function searchEditMembers() {
  const activity = selectedActivity.value
  if (!activity) {
    ElMessage.warning(t('activity.register.activityRequired'))
    return
  }
  const query = editSearchQuery.value.trim()
  if (!query) {
    ElMessage.info(t('activity.register.searchPlaceholder'))
    return
  }
  const seq = ++editSearchSeq
  editSearchLoading.value = true
  editSearchDone.value = false
  editSearchTruncated.value = false
  try {
    const found: ExistingMemberRow[] = []
    for (let page = 1; page <= 2; page += 1) {
      const { rows: members } = await fetchExistingMemberPage(activity._id, page, query)
      if (seq !== editSearchSeq) return
      found.push(...members)
      if (members.length < EXISTING_PAGE_SIZE) break
    }
    editSearchResults.value = found
    // 两页都拉满说明后面还有命中，结果被 200 条上限截断。
    editSearchTruncated.value = found.length >= EXISTING_PAGE_SIZE * 2
  } catch {
    if (seq !== editSearchSeq) return
    editSearchResults.value = []
    ElMessage.warning(t('activity.register.searchFailed'))
  } finally {
    if (seq === editSearchSeq) {
      editSearchLoading.value = false
      editSearchDone.value = true
    }
  }
}

function memberInEditGrid(item: ExistingMemberRow): boolean {
  const memberOid = String(item.member ?? '')
  const mode = normalizeMode(item.mode) ?? defaultMode()
  return rows.value.some((row) => row.oid === memberOid && row.mode === mode)
}

// 搜索结果「加入表格」：带着现有身份和时长落一行，管理员只改时长。
function addEditSearchResult(item: ExistingMemberRow) {
  const activity = selectedActivity.value
  if (!activity) return
  const memberOid = String(item.member ?? '')
  const mode = normalizeMode(item.mode) ?? defaultMode()
  if (!memberOid) return
  if (memberInEditGrid(item)) {
    ElMessage.info(t('activity.register.alreadyInGrid'))
    return
  }
  const blank = rows.value.find((row) => !hasContent(row))
  if (!blank && rows.value.length >= MAX_REGISTER_ROWS) {
    rowLimitWarning()
    return
  }
  const key = existingKey(memberOid, mode)
  const record: ExistingMemberRecord = existingMembers.value.get(key) ?? {
    sessionId: String(item._id),
    member: memberOid,
    mode,
    duration: Number(item.duration) || 0,
    studentId: normalizeStudentId(String(item.user?.id ?? '')),
    name: normalizePersonName(String(item.member_name ?? item.user?.name ?? ''))
  }
  existingMembers.value.set(key, record)
  recordHistory()
  const target = blank ?? createRow()
  if (!blank) rows.value.push(target)
  target.studentId = record.studentId
  target.name = record.name
  target.oid = record.member
  target.duration = String(record.duration)
  target.mode = record.mode
  target.note = ''
  target.candidates = []
  revalidateRow(target)
  markDuplicates()
  scheduleDraft()
}

async function retryLoadExisting() {
  const activity = selectedActivity.value
  if (!activity || existingLoading.value) return
  await loadExistingMembers(activity._id)
}

// 两栏切换：当前行先收进 stashedRows，再把另一栏换出来。
// 两栏各有自己的 100 行上限、自己的校验状态；活动、已录入索引和草稿共用。
//
// 只要 editorMode 真的变了，就把 URL 同步到对应模式（replace，不新增历史）：
// 用户点栏目、undo/redo 恢复历史栏目、OCR 识别完自动切到「添加」栏都算，
// 保证刷新/分享后打开的还是当前看到的那一栏。
// 唯一的例外是 route watch：URL 本身就是触发源，传入 syncUrl=false 避免回头再写一次。
function switchEditorMode(next: EditorMode, syncUrl = true) {
  if (next === editorMode.value) {
    if (syncUrl) replaceModeUrl(next)
    return
  }
  commitCellHistory()
  stashedRows[editorMode.value] = rows.value
  editorMode.value = next
  rows.value = stashedRows[next]
  if (!rows.value.length) {
    rows.value = Array.from({ length: 8 }, () => createRow())
  }
  activeCell.value = null
  rebuildDuplicateIndex()
  revalidateAll()
  applySavedState()
  scheduleDraft()
  if (syncUrl) replaceModeUrl(next)
}

// 栏目是 tablist：左右方向键在两栏之间移动，并把焦点跟到新栏目上。
function onEditorTabKeydown(event: KeyboardEvent, current: EditorMode) {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  event.preventDefault()
  const next: EditorMode = current === 'create' ? 'edit' : 'create'
  switchEditorMode(next)
  void nextTick(() => {
    document.querySelector<HTMLElement>(`[data-editor-tab="${next}"]`)?.focus()
  })
}

// 切换活动：草稿按活动隔离，换活动必须从白纸开始。
//
// 顺序很重要：
//   1. 当前表格内容写回「旧活动」的草稿键；
//   2. 清空所有上一个活动的痕迹（行、saved 状态、已有成员索引）；
//   3. 读「新活动」的草稿，没有就 resetRows(8) 全空白。
// 第 1 步和第 2 步的先后决定了会不会「录串」——先写回再清空，反过来就会把上一个
// 活动的行写进新活动的草稿。
async function switchActivity(nextId: string, record = true, preselect = false) {
  commitCellHistory()
  registrationContextReady = false
  const previousId = draftOwnerId
  // 切走之前先把当前内容存回旧活动。
  persistDraftFor(previousId, buildDraftPayload(previousId))
  const beforeMode = editorMode.value
  const beforeSwitch = snapshotRows()

  selectedActivityId.value = nextId
  draftOwnerId = nextId
  rows.value = []
  stashedRows.create = []
  stashedRows.edit = []
  rebuildDuplicateIndex()
  savedActivityId.value = ''
  savedCount.value = 0
  savedOids = new Set()
  resetExistingIndex()

  // 新活动自己的草稿；没有草稿就白纸起步。
  const payload = nextId ? readDraft(nextId) : null
  if (payload) applyDraft(payload)
  else resetRows(8)

  if (record) {
    // 用户主动换活动：历史栈重开一条，撤回可以回到刚才那个活动。
    undoStack.value = []
    redoStack.value = []
    pushUndoStack({
      activityId: previousId,
      mode: beforeMode,
      rows: beforeSwitch
    })
  }

  await hydrateSelectedActivity()
  const activity = selectedActivity.value
  if (activity && activity.type !== 'hybrid') {
    const mode = activity.type as RegistrationMode
    for (const row of rows.value) row.mode = mode
  }
  await syncActivityContext(preselect)
  registrationContextReady = true
  rebuildDuplicateIndex()
  revalidateAll()
}

async function onActivityChange() {
  // 先清空上一个活动的索引：换活动后旧的 sessionId 绝不能用于 PUT。
  await switchActivity(selectedActivityId.value)
}

function viewSavedActivity() {
  if (savedActivityId.value) {
    void router.push(`/activity/details/${savedActivityId.value}`)
  }
}

function rowLabel(row: RegistrationRow): string {
  const who = row.name.trim() || row.studentId.trim() || '未匹配成员'
  return row.studentId.trim() ? `${who}（${row.studentId.trim()}）` : who
}

// 覆盖确认：把「这次提交会动到哪些已有记录」摊开给用户看，确认后才写库。
// 用户点取消就直接中止，一个字节都不写。
function confirmOverwrite(hits: OverwritePlanRow[]): Promise<boolean> {
  overwriteRows.value = hits
  overwriteDialogVisible.value = true
  return new Promise<boolean>((resolve) => {
    overwriteResolver = resolve
  })
}

function resolveOverwrite(confirmed: boolean) {
  overwriteDialogVisible.value = false
  const resolver = overwriteResolver
  overwriteResolver = null
  resolver?.(confirmed)
}

function duplicateRowGroups(): RegistrationRow[][] {
  return [...duplicateGroups.values()].filter((group) => group.length > 1)
}

// 名单内重复（同一个人同一模式录了第二次）：先提示，确认后以靠后一行为准覆盖靠前行。
// 拒绝时保持原样，用户可以自己删掉多余的行再提交。
async function resolveDuplicateRows(): Promise<boolean> {
  const groups = duplicateRowGroups()
  if (!groups.length) return true
  const names = groups
    .map((group) => rowLabel(group[group.length - 1]))
    .slice(0, 5)
    .join('、')
  try {
    await ElMessageBox.confirm(
      t('activity.register.duplicateConfirmDesc', { count: groups.length, names }),
      t('activity.register.duplicateConfirmTitle'),
      {
        confirmButtonText: t('activity.register.duplicateConfirmAction'),
        cancelButtonText: t('activity.register.cancelAction'),
        type: 'warning'
      }
    )
  } catch {
    return false
  }
  collapseDuplicateRows()
  return true
}

// 保留靠前那一行的位置，用靠后一行的时长/备注覆盖它；身份字段两边本来就一致。
function collapseDuplicateRows() {
  if (!duplicateRowGroups().length) return
  recordHistory()
  const kept = new Map<string, RegistrationRow>()
  const dropped = new Set<RegistrationRow>()
  for (const row of rows.value) {
    const key = rowDuplicateKey(row)
    if (!key) continue
    const first = kept.get(key)
    if (!first) {
      kept.set(key, row)
      continue
    }
    // 第二次录入即覆盖：靠后一行填了值就以它为准，没填则保留靠前一行已有的值。
    if (row.duration.trim()) first.duration = row.duration
    if (row.note.trim()) first.note = row.note
    dropped.add(row)
  }
  if (!dropped.size) return
  rows.value = rows.value.filter((row) => !dropped.has(row))
  for (const row of dropped) dropDuplicateIndex(row)
  revalidateAll()
}

// 两栏查询重复：同一个人同一模式同时出现在「新增」和「修改」栏时，先 PUT 覆盖再整批新增
// 必然撞 409。duplicateGroups 只索引当前栏（collapseDuplicateRows 也只能跨单栏合并），
// 跨栏没法自动合并成一行，所以在提交前拦下并指名，让管理员删掉一栏里的行。
function findCrossColumnDuplicate(): RegistrationRow | null {
  const otherMode: EditorMode = editorMode.value === 'create' ? 'edit' : 'create'
  const other = new Map<string, RegistrationRow>()
  for (const row of stashedRows[otherMode]) {
    const key = rowDuplicateKey(row)
    if (key) other.set(key, row)
  }
  for (const row of pendingRows.value) {
    const key = rowDuplicateKey(row)
    if (key && other.has(key)) return row
  }
  return null
}

// 后端 409：索引没覆盖到的已有记录（超过回溯上限，或索引加载失败）。
function isConflictError(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 409
}

// 未在索引里的已有成员：确认后整批按「设置时长」覆盖，不再新增。
async function confirmUnindexedOverwrite(count: number): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      t('activity.register.conflictConfirmDesc', { count }),
      t('activity.register.conflictConfirmTitle'),
      {
        confirmButtonText: t('activity.register.confirmOverwrite'),
        cancelButtonText: t('activity.register.cancelAction'),
        type: 'warning'
      }
    )
    return true
  } catch {
    return false
  }
}

async function submitRegistration() {
  // 覆盖确认弹窗还开着时不接受第二次提交，避免把上一次等待的 Promise 顶掉。
  if (overwriteDialogVisible.value) return
  revalidateAll()
  if (!selectedActivity.value) {
    ElMessage.warning(t('activity.register.activityRequired'))
    return
  }
  if (existingLoading.value) {
    // 索引没拉完就提交，会把「改时长」误判成「新增」，v3 会累加导致时长翻倍。
    ElMessage.info(t('activity.register.existingLoading'))
    return
  }
  // 同一个人在同一张表里录了第二次：提示并用靠后一行覆盖。
  if (!(await resolveDuplicateRows())) return
  const crossDuplicate = findCrossColumnDuplicate()
  if (crossDuplicate) {
    ElMessage.error(
      t('activity.register.crossTabDuplicate', {
        name: rowLabel(crossDuplicate),
        mode: modeLabel(crossDuplicate.mode)
      })
    )
    return
  }
  if (!pendingRows.value.length) {
    ElMessage.info(t('activity.register.noPending'))
    return
  }
  // 单次提交人数上限：一次事务写太多既慢又容易超时，让管理员分批。
  if (pendingRows.value.length > MAX_SUBMIT_ROWS) {
    ElMessage.error(
      t('activity.register.submitLimitExceeded', {
        limit: MAX_SUBMIT_ROWS,
        count: pendingRows.value.length
      })
    )
    return
  }
  const invalid = pendingRows.value.filter(
    (row) => row.status !== 'ok' || !row.oid || durationValue(row) === null
  )
  if (invalid.length) {
    ElMessage.error(t('activity.register.invalidRowsRemain', { count: invalid.length }))
    return
  }

  const activity = selectedActivity.value
  // 分流：命中已有成员 → PUT 按设置语义覆盖；没命中 → v3 批量新增。
  const updates: Array<{ row: RegistrationRow; record: ExistingMemberRecord; duration: number }> =
    []
  const inserts: RegistrationRow[] = []
  const hits: OverwritePlanRow[] = []
  for (const row of pendingRows.value) {
    const record = existingOf(row)
    const duration = durationValue(row) as number
    if (!record) {
      inserts.push(row)
      continue
    }
    const changed = existingDurationChanged(row, record)
    hits.push({
      key: row.key,
      name: row.name.trim() || row.studentId.trim(),
      studentId: row.studentId.trim(),
      oldDuration: record.duration,
      newDuration: duration,
      changed
    })
    // 与现有时长一致也照写一次：「设置」语义下重写同样的值是幂等的，
    // 这样第二次录入已有人时行为统一——确认覆盖，而不是悄悄跳过。
    updates.push({ row, record, duration })
  }

  if (!updates.length && !inserts.length) {
    ElMessage.info(t('activity.register.nothingChanged'))
    return
  }

  // 命中已有记录 → 先给用户看对比再写。
  if (hits.length > 0) {
    const confirmed = await confirmOverwrite(hits)
    if (!confirmed) return
  } else if (existingTruncated.value) {
    // 索引被截断但本次没有已列出的命中：不再弹空表确认框，只提示未载入部分可能有重复。
    ElMessage.warning(
      t('activity.register.submitUnindexedNotice', { limit: EXISTING_MEMBER_LIMIT })
    )
  }

  submitLoading.value = true
  const failures: Array<{ label: string; reason: string }> = []
  let updated = 0
  let added = 0
  let finalized = false
  const finalizeSavedRows = () => {
    if (finalized || (!updated && !added)) return
    finalized = true
    savedActivityId.value = activity._id
    // 权威人数从成员分页 total 读取；新增、覆盖或部分成功后都重新校准缓存。
    void fetchExistingMemberPage(activity._id, 1)
      .then(({ total }) => {
        if (selectedActivityId.value === activity._id) savedCount.value = total
      })
      .catch(() => {
        // 保持当前缓存值；下一次载入活动时会重新从服务端校准。
      })
    for (const row of rows.value) {
      if (row.status === 'saved' && row.oid) savedOids.add(existingKey(row.oid, row.mode))
    }
    pruneSavedRows()
    editSearchResults.value = []
    editSearchDone.value = false
    if (added > 0 && activity.status === 'effective') {
      const pending = { ...activity, status: 'pending' as const }
      selectedActivity.value = pending
      activityOptions.value = activityOptions.value.map((item) =>
        item._id === pending._id ? pending : item
      )
    }
  }
  try {
    // PUT 逐行覆盖：小批并发，单行失败只影响自己，成功的不回滚。
    for (let start = 0; start < updates.length; start += UPDATE_CONCURRENCY) {
      const batch = updates.slice(start, start + UPDATE_CONCURRENCY)
      const results = await Promise.allSettled(
        batch.map((item) =>
          api.activity.duration.modify(
            item.record.sessionId,
            activity._id,
            item.duration,
            item.row.mode,
            { silent: true, timeout: BATCH_TIMEOUT }
          )
        )
      )
      results.forEach((result, index) => {
        const item = batch[index]
        if (result.status === 'fulfilled') {
          updated += 1
          existingMembers.value.set(existingKey(item.record.member, item.record.mode), {
            ...item.record,
            duration: item.duration
          })
          item.row.status = 'saved'
          item.row.message = t('activity.register.updatedRow')
          return
        }
        const reason = resolveErrorMessage(result.reason)
        failures.push({ label: rowLabel(item.row), reason })
        item.row.message = t('activity.register.updateFailed', { reason })
      })
    }

    if (inserts.length) {
      const payload: NewActivityMember[] = inserts.map((row) => ({
        member: row.oid,
        mode: row.mode,
        duration: durationValue(row) as number
      }))
      let inserted = false
      try {
        await api.activity.member.insertMany(activity._id, payload, {
          overwrite: false,
          rejectExisting: true,
          timeout: BATCH_TIMEOUT
        })
        inserted = true
      } catch (error) {
        if (!isConflictError(error)) throw error
        // 索引没覆盖到的已有记录（超过回溯上限 / 索引加载失败）：
        // 先确认，再把整批按「设置时长」写一遍，绝不静默累加。
        if (await confirmUnindexedOverwrite(payload.length)) {
          await api.activity.member.insertMany(activity._id, payload, {
            overwrite: true,
            timeout: BATCH_TIMEOUT
          })
          inserted = true
        } else {
          failures.push({
            label: t('activity.register.newMembersLabel'),
            reason: t('activity.register.conflictSkipped')
          })
        }
      }
      if (inserted) {
        added = payload.length
        for (const row of inserts) {
          row.status = 'saved'
          row.message = t('activity.register.insertedRow')
        }
      }
    }

    finalizeSavedRows()

    if (failures.length) {
      ElMessage.warning(
        t('activity.register.partialFailedSummary', {
          updated,
          added,
          failed: failures.length
        })
      )
      const lines = failures.slice(0, 10).map((item) => `${item.label}：${item.reason}`)
      if (failures.length > lines.length) {
        lines.push(t('activity.register.moreFailures', { count: failures.length - lines.length }))
      }
      await ElMessageBox.alert(lines.join('；'), t('activity.register.partialFailedTitle'), {
        confirmButtonText: '知道了'
      }).catch(() => undefined)
      return
    }

    // 成功提示区分「新增」与「覆盖已有」：两项相加就是本次写入的总条数。
    // 全量成功时 updated 恒等于 hits.length，所以覆盖数直接用 hits.length。
    const overwritten = hits.length
    const inserted = Math.max(updated + added - overwritten, 0)
    if (overwritten > 0) {
      ElMessage.success(
        t('activity.register.submitMixedSummary', {
          activity: activity.name,
          count: updated + added,
          inserted,
          overwritten
        })
      )
    } else {
      ElMessage.success(
        t('activity.register.submitSummary', {
          activity: activity.name,
          updated,
          added
        })
      )
    }
  } catch (error) {
    if (updated || added) {
      failures.push({ label: t('activity.register.newMembersLabel'), reason: resolveErrorMessage(error) })
      finalizeSavedRows()
      ElMessage.warning(
        t('activity.register.partialFailedSummary', {
          updated,
          added,
          failed: failures.length
        })
      )
    } else {
      ElMessage.error(resolveErrorMessage(error))
    }
  } finally {
    submitLoading.value = false
  }
}

// 已提交行不再展示：清掉后至少留一屏空白行，方便继续录入下一批。
function pruneSavedRows() {
  rows.value = rows.value.filter((row) => row.status !== 'saved')
  if (!rows.value.length) {
    resetRows(8)
    return
  }
  rebuildDuplicateIndex()
  revalidateAll()
}

function exportCsv() {
  const header = ['学号', '姓名', '班级', '时长', '模式', '备注', '匹配状态']
  const lines = rows.value
    .filter(hasContent)
    .map((row) =>
      [
        row.studentId,
        row.name,
        row.className,
        row.duration,
        modeLabel(row.mode),
        row.note,
        statusLabel(row.status)
      ]
        .map(csvCell)
        .join(',')
    )
  const content = `\ufeff${[header.map(csvCell).join(','), ...lines].join('\r\n')}`
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `义工登记-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}.csv`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function csvCell(value: string): string {
  const text = String(value ?? '')
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function statusLabel(status: RowStatus): string {
  if (status === 'ok') return '可提交'
  if (status === 'warning') return '待确认'
  if (status === 'error') return '错误'
  if (status === 'saved') return '已提交'
  return '待填写'
}

// 只清当前栏目：另一栏和活动选择都保留，避免误删正在改的另一批数据。
async function clearAll() {
  const label = editorMode.value === 'create' ? '新增' : '修改'
  try {
    await ElMessageBox.confirm(
      `清空后「${label}」栏当前内容不可恢复，确定继续？`,
      '清空当前栏目？',
      {
        confirmButtonText: '清空',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  recordHistory()
  rows.value = []
  resetRows(8)
  revalidateAll()
  scheduleDraft()
}

// ---------------------------------------------------------------------------
// 草稿：按活动隔离
//
// 键 = volunteer-register-draft-v2:<activityId>，未选活动时用 __none__。
// 换活动时先写回旧活动的键，再读新活动的键，两个活动的行永远不会互相串。
// ---------------------------------------------------------------------------
interface DraftPayload {
  mode?: EditorMode
  // 新增栏（旧草稿没有 editRows，读回时就只有这一栏）。
  rows?: Partial<RegistrationRow>[]
  // 修改栏。
  editRows?: Partial<RegistrationRow>[]
  activityId?: string
  savedActivityId?: string
  savedCount?: number
  savedOids?: string[]
}

function draftKey(activityId: string): string {
  return activityId ? `${DRAFT_KEY_PREFIX}${activityId}` : DRAFT_KEY_NONE
}

function readDraft(activityId: string): DraftPayload | null {
  try {
    const raw = localStorage.getItem(draftKey(activityId))
    if (!raw) return null
    return JSON.parse(raw) as DraftPayload
  } catch {
    return null
  }
}

// 旧版单活动草稿：只在升级后的第一次启动里读一次。
function readLegacyDraft(): DraftPayload | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY_LEGACY)
    if (!raw) return null
    return JSON.parse(raw) as DraftPayload
  } catch {
    return null
  }
}

function draftRowSnapshot(row: RegistrationRow): Partial<RegistrationRow> {
  return {
    studentId: row.studentId,
    name: row.name,
    classId: row.classId,
    className: row.className,
    oid: row.oid,
    duration: row.duration,
    mode: row.mode,
    note: row.note
  }
}

function buildDraftPayload(activityId: string): DraftPayload {
  // 两栏各自独立存档：当前显示的一栏在 rows 里，另一栏在 stashedRows 里。
  const createRows = editorMode.value === 'create' ? rows.value : stashedRows.create
  const editRows = editorMode.value === 'edit' ? rows.value : stashedRows.edit
  return {
    activityId,
    mode: editorMode.value,
    savedActivityId: savedActivityId.value,
    savedCount: savedCount.value,
    savedOids: [...savedOids],
    rows: createRows.map(draftRowSnapshot),
    editRows: editRows.map(draftRowSnapshot)
  }
}

function persistDraftFor(activityId: string, payload?: DraftPayload) {
  const data = payload ?? buildDraftPayload(activityId)
  try {
    localStorage.setItem(draftKey(activityId), JSON.stringify(data))
    if (activityId) localStorage.setItem(DRAFT_LAST_ACTIVITY_KEY, activityId)
  } catch {
    // 隐私模式或配额不足时保持页面可用；草稿只是便利功能。
  }
}

// 把草稿铺回表格：只取表格数据，状态字段重算。
function applyDraft(payload: DraftPayload) {
  savedActivityId.value = String(payload.savedActivityId ?? '')
  // 旧版草稿把“新增 + 修改”都累计进人数，不能继续恢复该错误值。
  savedCount.value = 0
  savedOids = new Set(
    Array.isArray(payload.savedOids)
      ? payload.savedOids.filter((oid): oid is string => typeof oid === 'string')
      : []
  )
  // 旧版草稿可能存着超过上限的行，也可能还留着已提交的人：读回时一并剔除。
  const mapDraftRows = (source: Partial<RegistrationRow>[]): RegistrationRow[] => {
    if (source.length > MAX_REGISTER_ROWS) {
      rowLimitWarning(source.length - MAX_REGISTER_ROWS)
    }
    return source
      .filter(
        (item) => !savedHas(String(item.oid ?? ''), normalizeMode(item.mode) ?? defaultMode())
      )
      .slice(0, MAX_REGISTER_ROWS)
      .map((item) =>
        createRow({
          studentId: normalizeStudentId(String(item.studentId ?? '')),
          name: normalizePersonName(String(item.name ?? '')),
          classId: String(item.classId ?? ''),
          className: String(item.className ?? ''),
          oid: String(item.oid ?? ''),
          duration: cleanDuration(String(item.duration ?? '')),
          mode: normalizeMode(item.mode) ?? defaultMode(),
          note: String(item.note ?? '')
        })
      )
  }
  stashedRows.create = mapDraftRows(Array.isArray(payload.rows) ? payload.rows : [])
  stashedRows.edit = mapDraftRows(Array.isArray(payload.editRows) ? payload.editRows : [])
  // 当前栏目由 URL 决定，草稿只负责铺两栏的行。旧草稿里的 mode 不能把
  // 直接访问 /activity/register/edit 的用户又拽回「添加」栏。
  rows.value = editorMode.value === 'create' ? stashedRows.create : stashedRows.edit
  if (!rows.value.length) resetRows(8)
  rebuildDuplicateIndex()
  if (registrationContextReady) revalidateAll()
  applySavedState()
}

function restoreDraft() {
  rowSeq = 0
  // 旧版草稿迁移：只在「还没选活动」的启动阶段迁移一次，随后删掉旧键。
  const legacy = readLegacyDraft()
  let targetId = ''
  if (legacy) {
    targetId = String(legacy.activityId ?? '')
    persistDraftFor(targetId, { ...legacy, activityId: targetId })
    localStorage.removeItem(DRAFT_KEY_LEGACY)
  } else {
    targetId = localStorage.getItem(DRAFT_LAST_ACTIVITY_KEY) ?? ''
  }
  draftOwnerId = targetId
  if (targetId) selectedActivityId.value = targetId
  const payload = targetId ? readDraft(targetId) : null
  if (payload) applyDraft(payload)
  else resetRows(8)
  draftReady = true
}

function writeDraft() {
  if (!draftReady) return
  persistDraftFor(draftOwnerId, buildDraftPayload(draftOwnerId))
}

function cancelIdleDraft() {
  if (draftIdleHandle !== undefined) {
    if (typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(draftIdleHandle)
    draftIdleHandle = undefined
  }
  if (draftIdleTimer !== undefined) {
    clearTimeout(draftIdleTimer)
    draftIdleTimer = undefined
  }
}

// 大表序列化 + localStorage 写入放空闲时段，避免击键后抢占主线程
function persistDraftIdle() {
  if (!draftReady) return
  cancelIdleDraft()
  if (typeof window.requestIdleCallback === 'function') {
    draftIdleHandle = window.requestIdleCallback(writeDraft, { timeout: 1000 })
  } else {
    draftIdleTimer = setTimeout(writeDraft, 200)
  }
}

// 离开页面/卸载时同步落盘一次，避免 debounce 窗口内的改动丢失
function flushDraft() {
  if (!draftReady) return
  clearTimeout(draftTimer)
  draftTimer = undefined
  cancelIdleDraft()
  writeDraft()
}

function scheduleDraft() {
  if (!draftReady) return
  clearTimeout(draftTimer)
  draftTimer = setTimeout(persistDraftIdle, 1500)
}

async function refreshIndex(showSuccess = true) {
  if (indexLoading.value) return
  indexLoading.value = true
  indexProgress.value = ''
  try {
    const next = await syncStudentIndex((loaded, total) => {
      indexProgress.value = total ? `${loaded}/${total}` : String(loaded)
    })
    indexState.value = next
    if (registrationContextReady) revalidateAll()
    if (next.error) ElMessage.warning(next.error)
    else if (showSuccess) ElMessage.success(`已同步 ${next.students.length} 名学生`)
  } catch {
    ElMessage.error('同步学生名单失败，请检查网络或权限')
  } finally {
    indexLoading.value = false
    indexProgress.value = ''
  }
}

async function initIndex() {
  indexBooting.value = true
  try {
    const cached = await loadStudentIndex()
    indexState.value = cached
    if (!cached.students.length || Date.now() - cached.updatedAt > INDEX_STALE_MS) {
      await refreshIndex(false)
    }
  } catch {
    ElMessage.warning('本地学生名单读取失败，可点击“同步名单”重试')
  } finally {
    indexBooting.value = false
  }
}

async function initPage() {
  // 准入判断与选择页共用同一套 helper（登录 + admin/department），
  // 未登录跳登录页、无权限跳 404 的行为与改造前一致。
  if (!(await ensureRegisterAccess(router))) return
  // URL 先定栏目，再读草稿：直接打开 /activity/register/edit 时不会被草稿带回「添加」。
  editorMode.value = modeFromRoute()
  restoreDraft()
  await searchActivities('')
  // 先铺本地名单，再补已有成员：本地名单负责姓名/班级，服务端索引负责 sessionId
  // 与「已录入」判定，两者都就绪后 revalidateAll 才能给出正确的行状态。
  await initIndex()
  const queryActivityId = routeActivityId()
  if (queryActivityId && queryActivityId !== draftOwnerId) {
    // 从活动详情/创建向导跳进来：切到这个活动，读它自己的草稿（不会带上前一个活动的行）
    await switchActivity(queryActivityId, false, true)
    return
  }
  await hydrateSelectedActivity()
  await syncActivityContext(false)
  registrationContextReady = true
  rebuildDuplicateIndex()
  revalidateAll()
}

function routeActivityId(): string {
  const raw = route.query.activity
  const id = Array.isArray(raw) ? raw[0] : raw
  return typeof id === 'string' ? id.trim() : ''
}

// URL → 栏目。/activity/register/:mode(create|edit) 把模式写进路径，
// 分享链接、刷新、前进后退都能还原到同一栏。
function modeFromRoute(): EditorMode {
  const raw = route.params.mode
  const value = Array.isArray(raw) ? raw[0] : raw
  return value === 'edit' ? 'edit' : 'create'
}

// 栏目 → URL。用 replace 而不是 push：在同一个编辑器里切栏目不该堆历史，
// 但地址栏必须立刻反映当前栏目；query（activity 等）原样保留。
function replaceModeUrl(next: EditorMode) {
  if (modeFromRoute() === next) return
  const query = { ...route.query }
  // 页面内换过活动时，URL 里的 activity 跟着当前选择走：
  // 否则从 B 活动切到修改栏后，地址栏还挂着进入页面时的 A。
  if (selectedActivityId.value) query.activity = selectedActivityId.value
  void router.replace({
    name: 'activity-register-mode',
    params: { mode: next },
    query
  })
}

// 顶部提示的「不再提示」：勾选后写进 localStorage，下次进来不再出现。
function loadDismissedNotices() {
  try {
    const raw = localStorage.getItem(NOTICE_DISMISSED_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      dismissedNotices.value = parsed.filter((key): key is string => typeof key === 'string')
    }
  } catch {
    dismissedNotices.value = []
  }
}

function dismissNotice(key: string) {
  if (dismissedNotices.value.includes(key)) return
  dismissedNotices.value = [...dismissedNotices.value, key]
  try {
    localStorage.setItem(NOTICE_DISMISSED_KEY, JSON.stringify(dismissedNotices.value))
  } catch {
    // 隐私模式或配额不足时保持页面可用。
  }
}

watch(rows, scheduleDraft, { deep: true })
watch(selectedActivityId, scheduleDraft)

// 浏览器前进/后退或直接改地址栏时，把 URL 里的栏目同步回编辑器。
// URL 已经是触发源，传 syncUrl=false：不再回头 replace 一次，避免自我循环。
watch(
  () => route.params.mode,
  () => {
    // 离开编辑器（例如被重定向到 404）时 params 会变空，不应再动栏目和草稿。
    if (route.name !== 'activity-register-mode') return
    const next = modeFromRoute()
    if (next !== editorMode.value) switchEditorMode(next, false)
  }
)

onMounted(() => {
  loadDismissedNotices()
  void initPage()
  void loadOcrEngines()
  window.addEventListener('pagehide', flushDraft)
  window.addEventListener('keydown', onGlobalKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('pagehide', flushDraft)
  window.removeEventListener('keydown', onGlobalKeydown)
  if (cellHistoryTimer) clearTimeout(cellHistoryTimer)
  releaseOcrImage()
  flushDraft()
})
</script>

<template>
  <div class="register-page">
    <ElPageHeader
      class="yg-page-header"
      :icon="ArrowLeft"
      @back="router.push({ name: 'activity-register', query: route.query })"
    >
      <template #content>
        <div class="page-head">
          <span class="page-title yg-page-header__title">{{ pageTitle }}</span>
          <!-- OCR 是页面的第一入口：放在左上角、蓝色实心、比其它按钮更显眼 -->
          <ElButton
            class="page-head__ocr"
            type="primary"
            size="large"
            :icon="UploadFilled"
            :loading="ocrLoading"
            @click="triggerOcr"
          >
            {{ t('activity.register.ocrAction') }}
          </ElButton>
          <!-- 引擎选择只影响 OCR 入队：留空表示按站点优先级自动回退 -->
          <ElSelect
            v-if="ocrEngineOptions.length"
            v-model="ocrEngine"
            class="ocr-engine-select"
            size="small"
            :aria-label="t('activity.register.ocrEngineLabel')"
            :title="t('activity.register.ocrEngineLabel')"
          >
            <ElOption :label="t('activity.register.ocrAutoOption')" value="" />
            <ElOption
              v-for="engine in ocrEngineOptions"
              :key="engine.value"
              :label="engine.label"
              :value="engine.value"
            />
          </ElSelect>
          <span v-if="ocrLoading && ocrStage" class="ocr-stage" role="status" aria-live="polite">
            {{ ocrStage }}
          </span>
          <!-- 名单没就绪时常驻的紧凑提示：没名单就会录错人，这条不能被收起来 -->
          <span v-if="listNoticeActive" class="list-hint" role="status">
            <ElIcon class="list-hint__icon"><WarningFilled /></ElIcon>
            <span class="list-hint__text">{{ t('activity.register.noticeListShort') }}</span>
          </span>
        </div>
      </template>
      <template #extra>
        <div class="index-status yg-page-header__actions">
          <!-- 顶部三条提示默认收进这个图标按钮；三条都被勾掉后按钮消失 -->
          <ElPopover
            v-if="noticeButtonVisible"
            v-model:visible="noticeVisible"
            placement="bottom-end"
            trigger="click"
            width="360px"
            popper-class="vr-notice-popper"
          >
            <template #reference>
              <ElButton
                class="notice-trigger"
                :type="noticeButtonType"
                :icon="Bell"
                circle
                :aria-label="t('activity.register.noticeTrigger')"
                :title="t('activity.register.noticeTrigger')"
              />
            </template>
            <div class="notice-panel">
              <div
                v-for="notice in registerNotices"
                :key="notice.key"
                class="notice-item"
                :class="`notice-item--${notice.type}`"
              >
                <div class="notice-item__head">
                  <ElIcon class="notice-item__icon">
                    <WarningFilled v-if="notice.type === 'warning'" />
                    <InfoFilled v-else />
                  </ElIcon>
                  <span class="notice-item__title">{{ notice.title }}</span>
                </div>
                <p class="notice-item__desc">{{ notice.description }}</p>
                <ElCheckbox
                  class="notice-item__dismiss"
                  :model-value="dismissedNotices.includes(notice.key)"
                  @change="dismissNotice(notice.key)"
                >
                  {{ t('activity.register.noticeNeverShow') }}
                </ElCheckbox>
              </div>
            </div>
          </ElPopover>
          <ElTag :type="indexTagType" effect="plain">{{ indexLabel }}</ElTag>
          <span class="index-meta">
            {{ students.length }} 人
            <template v-if="indexUpdatedLabel"> · {{ indexUpdatedLabel }}</template>
          </span>
          <ElButton
            size="small"
            :icon="Refresh"
            :loading="indexLoading || indexBooting"
            @click="refreshIndex()"
          >
            同步名单
          </ElButton>
        </div>
      </template>
    </ElPageHeader>

    <div class="reg-stack">
      <section class="reg-card activity-card">
        <div class="activity-picker">
          <div class="field-label">目标义工活动</div>
          <ElSelect
            v-model="selectedActivityId"
            class="activity-select"
            filterable
            remote
            reserve-keyword
            :remote-method="searchActivities"
            :loading="activityLoading"
            placeholder="搜索并选择活动"
            @change="onActivityChange"
          >
            <ElOption
              v-for="activity in activityOptions"
              :key="activity._id"
              :label="activityLabel(activity)"
              :value="activity._id"
            />
          </ElSelect>
        </div>
        <div v-if="selectedActivity" class="activity-summary">
          <ElTag :type="activityStatusType(selectedActivity.status)" effect="plain">
            {{ activityStatusLabel(selectedActivity.status) }}
          </ElTag>
          <span class="activity-name">{{ selectedActivity.name }}</span>
          <span class="muted">{{ String(selectedActivity.date || '').slice(0, 10) }}</span>
          <span v-if="selectedActivity.status === 'pending'" class="muted">
            {{ t('activity.register.pendingSummary') }}
          </span>
          <span v-else class="muted">提交时写入该活动的成员时长</span>
          <span v-if="existingLoading" class="muted existing-index-status" role="status">
            {{ t('activity.register.existingLoading') }}
          </span>
          <span v-else-if="existingFetchFailed" class="existing-index-status is-error">
            {{ t('activity.register.existingLoadFailedShort') }}
            <ElButton link type="primary" size="small" @click="retryLoadExisting">
              {{ t('activity.register.retryLoad') }}
            </ElButton>
          </span>
          <span v-else-if="existingMembers.size" class="muted existing-index-status">
            {{ t('activity.register.existingLoaded', { count: existingMembers.size }) }}
          </span>
        </div>
        <div v-else class="activity-summary muted">
          选择活动后才能提交；未选择时仍可编辑、粘贴和导出。
        </div>
      </section>

      <!-- 两个栏目共用下面这张表：点哪个栏目就显示哪个，另一个先收起来 -->
      <section class="reg-card editor-tabs-card">
        <div class="editor-tabs" role="tablist" aria-label="义工登记栏目">
          <button
            type="button"
            role="tab"
            class="editor-tab"
            :class="{ 'is-active': editorMode === 'create' }"
            :aria-selected="editorMode === 'create'"
            :tabindex="editorMode === 'create' ? 0 : -1"
            data-editor-tab="create"
            @click="switchEditorMode('create')"
            @keydown="onEditorTabKeydown($event, 'create')"
          >
            <span class="editor-tab__title">{{ t('nav.registerEntry.create.title') }}</span>
            <span class="editor-tab__desc">录入活动里还没有的人，每批最多 100 人</span>
          </button>
          <button
            type="button"
            role="tab"
            class="editor-tab"
            :class="{ 'is-active': editorMode === 'edit' }"
            :aria-selected="editorMode === 'edit'"
            :tabindex="editorMode === 'edit' ? 0 : -1"
            data-editor-tab="edit"
            @click="switchEditorMode('edit')"
            @keydown="onEditorTabKeydown($event, 'edit')"
          >
            <span class="editor-tab__title">{{ t('nav.registerEntry.edit.title') }}</span>
            <span class="editor-tab__desc"
              >搜索或录入名单里的人，提交按填写时长覆盖，每批最多 100 人</span
            >
          </button>
        </div>
      </section>

      <!-- 修改栏专属：从服务器按姓名/学号查已录入成员，点「加入表格」后改时长 -->
      <section v-if="editorMode === 'edit'" class="reg-card edit-search-card">
        <div class="edit-search-head">
          <div>
            <div class="grid-title">搜索已录入义工</div>
            <div class="muted">按姓名或学号搜索当前活动成员；也可以直接在下方表格里录入。</div>
          </div>
          <div class="edit-search-controls">
            <ElInput
              v-model="editSearchQuery"
              class="edit-search-input"
              :placeholder="t('activity.register.searchPlaceholder')"
              clearable
              :disabled="!selectedActivity"
              @keydown.enter="searchEditMembers"
            />
            <ElButton
              type="primary"
              :icon="Search"
              :loading="editSearchLoading"
              :disabled="!selectedActivity"
              @click="searchEditMembers"
            >
              搜索
            </ElButton>
          </div>
        </div>
        <p v-if="editSearchTruncated" class="edit-search-truncated">
          {{ t('activity.register.searchTruncated') }}
        </p>
        <div v-if="editSearchResults.length" class="edit-search-results">
          <div v-for="item in editSearchResults" :key="item._id" class="edit-search-item">
            <span class="edit-search-item__name">
              {{ item.member_name || item.user?.name || '未知成员' }}
            </span>
            <span class="edit-search-item__id">{{ item.user?.id || '学号暂缺' }}</span>
            <ElTag size="small" effect="plain">{{ modeLabel(item.mode) }}</ElTag>
            <span class="edit-search-item__duration">{{ item.duration }} 小时</span>
            <ElButton
              size="small"
              type="primary"
              plain
              :disabled="memberInEditGrid(item)"
              @click="addEditSearchResult(item)"
            >
              {{ memberInEditGrid(item) ? '已加入' : '加入表格' }}
            </ElButton>
          </div>
        </div>
        <div v-else-if="editSearchDone" class="edit-search-empty">没有找到匹配的已录入成员</div>
      </section>

      <section class="reg-card toolbar-card">
        <ElButton :icon="CopyDocument" @click="pasteFromClipboard">从剪贴板粘贴</ElButton>
        <ElButton :icon="UploadFilled" @click="triggerImport">导入表格</ElButton>
        <ElButton :icon="Refresh" :loading="indexLoading || indexBooting" @click="refreshIndex()">
          同步名单
        </ElButton>
        <ElButton :icon="Download" @click="exportCsv">导出 CSV</ElButton>
        <ElButton v-if="savedActivityId" :icon="View" @click="viewSavedActivity">
          查看活动（{{ savedCount }} 人）
        </ElButton>
        <span class="toolbar-spacer" />
        <div class="history-group">
          <ElButton
            :icon="RefreshLeft"
            :disabled="!canUndo"
            :title="`${t('activity.register.undo')}（Ctrl/Cmd+Z）`"
            :aria-label="t('activity.register.undo')"
            circle
            @click="undo()"
          />
          <ElButton
            :icon="RefreshRight"
            :disabled="!canRedo"
            :title="`${t('activity.register.redo')}（Ctrl/Cmd+Shift+Z）`"
            :aria-label="t('activity.register.redo')"
            circle
            @click="redo()"
          />
        </div>
        <ElButton type="danger" plain @click="clearAll">清空</ElButton>
        <ElButton
          type="primary"
          :loading="submitLoading"
          :disabled="!canSubmit"
          @click="submitRegistration"
        >
          {{ editorMode === 'create' ? '提交新增' : '提交修改' }}
        </ElButton>
        <input
          ref="importInput"
          type="file"
          accept=".xlsx,.xls,.csv,.tsv,.txt,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv,text/tab-separated-values,text/plain"
          hidden
          @change="onImportFile"
        />
        <input
          ref="ocrInput"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          hidden
          @change="onOcrFile"
        />
      </section>

      <section class="reg-card grid-card">
        <div class="grid-head">
          <div>
            <div class="grid-title">
              {{ editorMode === 'create' ? '新增明细' : '修改明细' }}
            </div>
            <div class="grid-sub">
              支持从 Excel 复制多行粘贴；学号/姓名会自动互相补全并标出同名或不一致。{{
                t('activity.register.overrideNotice')
              }}
              <template v-if="editorMode === 'edit'">
                这里只处理已在活动中的成员：提交时按本行时长覆盖，未在活动中的行会标错。
              </template>
            </div>
          </div>
          <div class="summary-strip">
            <div class="summary-item">
              <span>总行数</span>
              <strong class="yg-text-num">{{ stats.total }}</strong>
            </div>
            <div v-if="stats.existing" class="summary-item">
              <span>{{ t('activity.register.existingBadge') }}</span>
              <strong class="saved yg-text-num">{{ stats.existing }}</strong>
            </div>
            <div class="summary-item">
              <span>待提交</span>
              <strong class="ok yg-text-num">{{ stats.ready }}</strong>
            </div>
            <div v-if="stats.saved" class="summary-item">
              <span>已提交</span>
              <strong class="saved yg-text-num">{{ stats.saved }}</strong>
            </div>
            <div v-if="stats.duplicate" class="summary-item">
              <span>重复</span>
              <strong class="warning yg-text-num">{{ stats.duplicate }}</strong>
            </div>
            <div class="summary-item">
              <span>待确认</span>
              <strong class="warning yg-text-num">{{ stats.warning }}</strong>
            </div>
            <div class="summary-item">
              <span>错误</span>
              <strong class="error yg-text-num">{{ stats.error }}</strong>
            </div>
          </div>
        </div>

        <div class="grid-wrap" @paste="onGridPaste">
          <table class="reg-grid">
            <thead>
              <tr>
                <th class="rownum-col">#</th>
                <th class="id-col">学号</th>
                <th class="name-col">姓名</th>
                <th class="class-col">班级</th>
                <th class="duration-col">时长 (h)</th>
                <th class="mode-col">模式</th>
                <th class="note-col">备注（仅导出）</th>
                <th class="status-col">状态</th>
                <th class="action-col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, r) in rows"
                :key="row.key"
                :class="{
                  'row-error': row.status === 'error',
                  'row-warning': row.status === 'warning',
                  'row-saved': row.status === 'saved'
                }"
              >
                <td class="rownum">{{ r + 1 }}</td>
                <td>
                  <div class="cell-wrap">
                    <input
                      class="cell-input"
                      :class="{
                        'is-error': row.status === 'error',
                        'is-warning': row.status === 'warning'
                      }"
                      :data-cell="`${r}-0`"
                      :value="row.studentId"
                      inputmode="numeric"
                      autocomplete="off"
                      placeholder="学号"
                      aria-label="学号"
                      :aria-invalid="row.status === 'error'"
                      @input="onIdInput(row, $event)"
                      @focus="onCellFocus(r, 0)"
                      @keydown="onGridKeydown($event, r, 0)"
                    />
                    <div v-if="showCandidates(r, 0)" class="candidate-menu">
                      <button
                        v-for="candidate in row.candidates"
                        :key="candidate.oid"
                        type="button"
                        class="candidate-item"
                        @mousedown.prevent="applyCandidate(row, candidate)"
                      >
                        <span class="candidate-name">{{ candidate.name }}</span>
                        <span class="candidate-meta">
                          {{ candidate.studentId }} · {{ candidate.className || '未分班' }}
                        </span>
                      </button>
                    </div>
                  </div>
                </td>
                <td>
                  <div class="cell-wrap">
                    <input
                      class="cell-input"
                      :class="{
                        'is-error': row.status === 'error',
                        'is-warning': row.status === 'warning'
                      }"
                      :data-cell="`${r}-1`"
                      :value="row.name"
                      autocomplete="off"
                      placeholder="姓名"
                      aria-label="姓名"
                      :aria-invalid="row.status === 'error'"
                      @input="onNameInput(row, $event)"
                      @focus="onCellFocus(r, 1)"
                      @keydown="onGridKeydown($event, r, 1)"
                    />
                    <div v-if="showCandidates(r, 1)" class="candidate-menu">
                      <button
                        v-for="candidate in row.candidates"
                        :key="candidate.oid"
                        type="button"
                        class="candidate-item"
                        @mousedown.prevent="applyCandidate(row, candidate)"
                      >
                        <span class="candidate-name">{{ candidate.name }}</span>
                        <span class="candidate-meta">
                          {{ candidate.studentId }} · {{ candidate.className || '未分班' }}
                        </span>
                      </button>
                    </div>
                  </div>
                </td>
                <td>
                  <div class="class-cell" :title="row.className || '—'">
                    {{ row.className || '—' }}
                  </div>
                </td>
                <td>
                  <input
                    class="cell-input duration-input"
                    :class="{
                      'is-error': row.status === 'error',
                      'is-warning': row.status === 'warning'
                    }"
                    :data-cell="`${r}-2`"
                    :value="row.duration"
                    inputmode="decimal"
                    autocomplete="off"
                    placeholder="0.0"
                    aria-label="时长"
                    :aria-invalid="row.status === 'error'"
                    @input="onDurationInput(row, $event)"
                    @focus="onCellFocus(r, 2)"
                    @keydown="onGridKeydown($event, r, 2)"
                  />
                </td>
                <td>
                  <div v-if="existingOf(row)" class="mode-locked">
                    <ElTag type="info" effect="plain" size="small">
                      {{ modeLabel(row.mode) }}
                    </ElTag>
                  </div>
                  <select
                    v-else
                    v-model="row.mode"
                    class="cell-select"
                    :data-cell="`${r}-3`"
                    aria-label="模式"
                    :aria-invalid="row.status === 'error'"
                    @change="onModeChange(row)"
                    @focus="onCellFocus(r, 3)"
                    @keydown="onGridKeydown($event, r, 3)"
                  >
                    <option v-for="mode in modeOptions" :key="mode.value" :value="mode.value">
                      {{ mode.label }}
                    </option>
                  </select>
                </td>
                <td>
                  <input
                    v-model="row.note"
                    class="cell-input"
                    :data-cell="`${r}-4`"
                    autocomplete="off"
                    placeholder="可选"
                    aria-label="备注"
                    @focus="onCellFocus(r, 4)"
                    @keydown="onGridKeydown($event, r, 4)"
                  />
                </td>
                <td>
                  <div class="status-cell">
                    <div class="status-badges">
                      <span class="status-pill" :class="`status-${row.status}`">
                        {{ statusLabel(row.status) }}
                      </span>
                      <ElTag v-if="existingOf(row)" size="small" type="primary" effect="plain">
                        {{ t('activity.register.existingBadge') }}
                      </ElTag>
                    </div>
                    <!-- 其他模式已有记录只提示不阻断：状态仍是可提交 -->
                    <span
                      v-if="otherModeHint(row)"
                      class="status-msg status-msg--warning"
                      :title="otherModeHint(row)"
                    >
                      {{ otherModeHint(row) }}
                    </span>
                    <span
                      v-if="existingHint(row) || row.message"
                      class="status-msg"
                      :title="[existingHint(row), row.message].filter(Boolean).join('；')"
                    >
                      {{ [existingHint(row), row.message].filter(Boolean).join('；') }}
                    </span>
                  </div>
                </td>
                <td>
                  <div class="row-actions">
                    <ElButton
                      text
                      circle
                      size="small"
                      :icon="CopyDocument"
                      title="复制行"
                      aria-label="复制行"
                      @click="duplicateRow(r)"
                    />
                    <ElButton
                      text
                      circle
                      size="small"
                      type="danger"
                      :icon="Delete"
                      title="删除行"
                      aria-label="删除行"
                      @click="removeRow(r)"
                    />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="grid-footer">
          <ElButton type="primary" plain :icon="Plus" @click="addRow">添加行</ElButton>
        </div>
      </section>
    </div>

    <!-- 覆盖确认：把这次提交会动到的已有记录摊开，确认后才写库 -->
    <ElDialog
      v-model="overwriteDialogVisible"
      class="vr-overwrite-dialog"
      width="560px"
      :title="t('activity.register.overwriteConfirmTitle', { count: overwriteRows.length })"
      @close="resolveOverwrite(false)"
    >
      <p class="overwrite-desc">{{ t('activity.register.overwriteConfirmDesc') }}</p>
      <p v-if="existingTruncated" class="overwrite-truncated">
        {{ t('activity.register.overwriteTruncatedNotice', { limit: EXISTING_MEMBER_LIMIT }) }}
      </p>
      <div v-if="overwriteRows.length" class="overwrite-table-wrap">
        <table class="overwrite-table">
          <thead>
            <tr>
              <th>{{ t('activity.register.overwriteColName') }}</th>
              <th>{{ t('activity.register.overwriteColStudentId') }}</th>
              <th class="is-num">{{ t('activity.register.overwriteColOldDuration') }}</th>
              <th class="is-num">{{ t('activity.register.overwriteColNewDuration') }}</th>
              <th>{{ t('activity.register.overwriteColAction') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in overwriteRows" :key="item.key">
              <td>{{ item.name || '—' }}</td>
              <td class="is-num">{{ item.studentId || '—' }}</td>
              <td class="is-num">{{ item.oldDuration }}</td>
              <td class="is-num">{{ item.newDuration }}</td>
              <td>
                <span class="overwrite-action" :class="item.changed ? 'is-overwrite' : 'is-skip'">
                  {{
                    item.changed
                      ? t('activity.register.overwriteAction')
                      : t('activity.register.overwriteSkipAction')
                  }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <ElButton @click="resolveOverwrite(false)">
          {{ t('activity.register.cancelAction') }}
        </ElButton>
        <ElButton type="danger" @click="resolveOverwrite(true)">
          {{ t('activity.register.confirmOverwrite') }}
        </ElButton>
      </template>
    </ElDialog>

    <!-- OCR 结果确认：左侧图片框选校对，右侧逐行改文字，底部是解析预览 -->
    <ElDialog
      v-model="ocrReviewVisible"
      class="vr-ocr-dialog"
      width="min(1100px, 96vw)"
      top="6vh"
      destroy-on-close
      :close-on-click-modal="false"
      :title="t('activity.register.ocrReviewTitle')"
      @closed="resetOcrReview"
    >
      <div class="ocr-review">
        <div class="ocr-review__head">
          <span v-if="ocrReviewEngine" class="ocr-review__meta">
            {{ t('ai.ocr.engine') }}：{{ ocrReviewEngineLabel }}
          </span>
          <span
            v-if="ocrReviewConfidence !== undefined"
            class="ocr-review__meta"
            :class="{ 'is-low': ocrReviewConfidence > 0 && ocrReviewConfidence < 0.6 }"
          >
            {{ t('ai.ocr.confidence') }}：{{ Math.round(ocrReviewConfidence * 100) }}%
          </span>
          <span class="ocr-review__meta">
            {{ t('activity.register.ocrReviewSelected', { count: ocrReviewSelectedCount }) }}
          </span>
          <span class="ocr-review__spacer" />
          <ElSelect
            v-model="ocrEngine"
            class="ocr-review__engine"
            size="small"
            :disabled="ocrReviewLoading"
            :aria-label="t('activity.register.ocrEngineLabel')"
          >
            <ElOption :label="t('activity.register.ocrAutoOption')" value="" />
            <ElOption
              v-for="engine in ocrEngineOptions"
              :key="engine.value"
              :label="engine.label"
              :value="engine.value"
            />
          </ElSelect>
          <ElButton
            size="small"
            :icon="Refresh"
            :loading="ocrReviewLoading"
            @click="rerunOcrReview"
          >
            {{ t('activity.register.ocrReviewRerun') }}
          </ElButton>
        </div>
        <p v-if="ocrReviewLoading && ocrStage" class="ocr-review__stage" role="status">
          {{ ocrStage }}
        </p>

        <div class="ocr-review__body">
          <section class="ocr-review__pane">
            <div class="ocr-review__pane-title">
              {{ t('activity.register.ocrReviewImageTitle') }}
            </div>
            <div v-if="ocrReviewImageUrl" class="ocr-canvas">
              <div class="ocr-canvas__frame">
                <img
                  :src="ocrReviewImageUrl"
                  class="ocr-canvas__img"
                  :alt="t('activity.register.ocrReviewImageAlt')"
                  @load="onOcrReviewImageLoad"
                />
                <div v-if="ocrReviewImageSize.width" class="ocr-canvas__overlay">
                  <button
                    v-for="(line, index) in ocrReviewLines"
                    v-show="line.box"
                    :key="line.key"
                    type="button"
                    class="ocr-box"
                    :class="{ 'is-selected': line.selected }"
                    :style="line.box ? ocrBoxStyle(line.box) : undefined"
                    :title="line.text"
                    :aria-label="line.text"
                    :aria-pressed="line.selected"
                    @click="toggleOcrReviewLine(index)"
                  />
                </div>
              </div>
            </div>
            <p v-else-if="ocrReviewImageFailed" class="ocr-review__hint">
              {{ t('activity.register.ocrReviewImageFailed') }}
            </p>
            <p v-else class="ocr-review__hint">
              {{ t('activity.register.ocrReviewImageLoading') }}
            </p>
            <p v-if="!ocrReviewHasBox" class="ocr-review__hint">
              {{ t('activity.register.ocrReviewNoBox') }}
            </p>
          </section>

          <section class="ocr-review__pane">
            <div class="ocr-review__pane-title">
              {{ t('activity.register.ocrReviewLinesTitle') }}
              <span class="ocr-review__count">{{ ocrReviewLines.length }}</span>
              <span class="ocr-review__spacer" />
              <ElButton text size="small" @click="setAllOcrReviewLines(true)">
                {{ t('activity.register.ocrReviewSelectAll') }}
              </ElButton>
              <ElButton text size="small" @click="setAllOcrReviewLines(false)">
                {{ t('activity.register.ocrReviewClearAll') }}
              </ElButton>
            </div>
            <div class="ocr-line-list">
              <div
                v-for="line in ocrReviewLines"
                :key="line.key"
                class="ocr-line"
                :class="{ 'is-excluded': !line.selected }"
              >
                <ElCheckbox v-model="line.selected" />
                <ElInput v-model="line.text" size="small" class="ocr-line__input" />
                <span
                  v-if="line.confidence !== null && line.confidence < 0.6"
                  class="ocr-line__low"
                >
                  {{ Math.round(line.confidence * 100) }}%
                </span>
              </div>
              <p v-if="!ocrReviewLines.length" class="ocr-review__hint">
                {{ t('activity.register.ocrReviewNoText') }}
              </p>
            </div>
          </section>
        </div>

        <section class="ocr-review__preview">
          <div class="ocr-review__pane-title">
            {{ t('activity.register.ocrReviewPreviewTitle') }}
          </div>
          <ul v-if="ocrReviewWarnings.length" class="ocr-review__warnings">
            <li v-for="warning in ocrReviewWarnings" :key="warning">{{ warning }}</li>
          </ul>
          <p v-if="ocrReviewOverLimit" class="ocr-review__warn">
            {{ t('activity.register.rowLimitReached', { limit: MAX_REGISTER_ROWS }) }}
          </p>
          <p v-else-if="!ocrReviewCanApply" class="ocr-review__hint">
            {{ t('activity.register.ocrReviewEmpty') }}
          </p>
          <div v-if="ocrReviewRows.length" class="ocr-review__table-wrap">
            <table class="ocr-review__table">
              <thead>
                <tr>
                  <th class="is-num">#</th>
                  <th>{{ t('activity.register.overwriteColStudentId') }}</th>
                  <th>{{ t('activity.register.overwriteColName') }}</th>
                  <th class="is-num">时长</th>
                  <th>模式</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, index) in ocrReviewRows" :key="index">
                  <td class="is-num">{{ index + 1 }}</td>
                  <td class="is-num">{{ row.studentId || '—' }}</td>
                  <td>{{ row.name || '—' }}</td>
                  <td class="is-num">{{ row.duration ?? '—' }}</td>
                  <td>{{ row.mode ? modeLabel(row.mode) : '—' }}</td>
                  <td>{{ row.note || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <template #footer>
        <ElButton @click="closeOcrReview">
          {{ t('activity.register.cancelAction') }}
        </ElButton>
        <ElButton
          type="primary"
          :disabled="!ocrReviewCanApply || ocrReviewLoading"
          @click="confirmOcrReview"
        >
          {{ t('activity.register.ocrReviewApply') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
/* EP 主题变量由 element-overrides.css 全局定义（含单调色阶），页面不再局部覆盖：
   旧实现把 primary 的 light-5/7/8/9 全压成 primary-soft，会让本页 EP 浅色阶退回
   「文字与底同色」的老问题。这里只保留本页确实需要的圆角。 */
.register-page {
  box-sizing: border-box;
  width: 100%;
  max-width: 80rem;
  margin: 0 auto;
  padding: var(--yg-space-4) var(--yg-space-6) var(--yg-space-7);
  --el-border-radius-base: var(--yg-radius-md);
}

.page-title {
  font-size: var(--yg-font-size-3xl);
  font-weight: var(--yg-font-weight-semibold);
}

/* 页头左侧：标题 + 主操作（OCR）+ 常驻的名单提示 */
.page-head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  flex-wrap: wrap;
}

.page-head__ocr {
  box-shadow: var(--yg-shadow-sm);
}

.list-hint {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  padding: 0 var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  white-space: nowrap;
}

.list-hint__icon {
  font-size: var(--yg-font-size-sm);
}

.notice-trigger {
  flex: none;
}

.notice-panel {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
}

.notice-item {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
}

.notice-item__head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.notice-item__icon {
  font-size: var(--yg-font-size-md);
}

.notice-item--warning .notice-item__icon,
.notice-item--warning .notice-item__title {
  color: var(--yg-text-warning);
}

.notice-item--info .notice-item__icon,
.notice-item--info .notice-item__title {
  color: var(--yg-text-link);
}

.notice-item__title {
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-semibold);
}

.notice-item__desc {
  margin: 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
}

.notice-item__dismiss {
  align-self: flex-start;
  height: auto;
}

.index-status {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  flex-wrap: wrap;
}

.index-meta {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
}

.reg-stack {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  margin-top: var(--yg-space-2);
}

.reg-card {
  background: var(--yg-bg-surface);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-lg);
  box-shadow: var(--yg-shadow-sm);
}

.activity-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-4) var(--yg-space-6);
  padding: var(--yg-space-4);
}

/* 两栏切换：选中项用主色描边 + 浅底，未选中保持平面，点哪个显示哪个 */
.editor-tabs-card {
  padding: var(--yg-space-2);
}

.editor-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--yg-space-2);
}

.editor-tab {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--yg-space-1);
  min-height: var(--yg-control-height-lg);
  padding: var(--yg-space-3) var(--yg-space-4);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface);
  color: var(--yg-text-secondary);
  text-align: left;
  cursor: pointer;
  transition:
    border-color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out),
    box-shadow var(--yg-dur-base) var(--yg-ease-out);
}

.editor-tab:hover {
  border-color: var(--yg-border-strong);
  background: var(--yg-bg-hover);
}

.editor-tab:active {
  background: var(--yg-bg-active);
  box-shadow: var(--yg-shadow-inset);
}

.editor-tab.is-active {
  border-color: var(--yg-color-primary);
  background: var(--yg-color-primary-soft);
  color: var(--yg-text-primary);
  box-shadow: var(--yg-shadow-sm);
}

.editor-tab:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.editor-tab:disabled,
.editor-tab.is-disabled {
  border-color: var(--yg-border-subtle);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-disabled-control);
  cursor: not-allowed;
  box-shadow: none;
}

.editor-tab__title {
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
}

.editor-tab__desc {
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-tertiary);
}

@media (max-width: 768px) {
  .editor-tabs {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .editor-tab {
    transition: none;
  }
}

@supports (corner-shape: squircle) {
  .editor-tab {
    corner-shape: squircle;
  }
}

/* 修改栏专属搜索：输入 + 结果行，一行一个「加入表格」 */
.edit-search-card {
  padding: var(--yg-space-4);
}

.edit-search-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--yg-space-3);
  flex-wrap: wrap;
}

.edit-search-controls {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.edit-search-input {
  width: min(20rem, 55vw);
}

.edit-search-results {
  margin-top: var(--yg-space-3);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  overflow: hidden;
}

.edit-search-truncated {
  margin: var(--yg-space-3) 0 0;
  padding: var(--yg-space-2) var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.edit-search-item {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  min-height: 2.5rem;
  padding: var(--yg-space-1) var(--yg-space-3);
  border-bottom: 1px solid var(--yg-border-subtle);
}

.edit-search-item:last-child {
  border-bottom: none;
}

.edit-search-item__name {
  min-width: 5rem;
  font-weight: var(--yg-font-weight-medium);
}

.edit-search-item__id {
  color: var(--yg-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.edit-search-item__duration {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.edit-search-empty {
  padding: var(--yg-space-3) 0 0;
  color: var(--yg-text-tertiary);
}

.activity-picker {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  min-width: 22rem;
}

.field-label {
  flex: none;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
}

.activity-select {
  width: 24rem;
  max-width: 100%;
}

.activity-summary {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  min-width: 0;
  flex-wrap: wrap;
}

.activity-name {
  font-weight: var(--yg-font-weight-semibold);
}

/* 已录入成员索引的加载/失败/就绪状态：贴在活动摘要行尾，不额外占一张卡片。 */
.existing-index-status {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
}

.existing-index-status.is-error {
  color: var(--el-color-danger);
}

.muted {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

.ocr-stage {
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
}

.ocr-engine-select {
  width: 11rem;
}

/* OCR 结果确认弹窗：左图右字、底部预览 */
.ocr-review {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
}

.ocr-review__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-3);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.ocr-review__meta {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  font-variant-numeric: tabular-nums;
}

.ocr-review__meta.is-low {
  color: var(--yg-text-warning);
}

.ocr-review__spacer {
  flex: 1 1 var(--yg-space-4);
}

.ocr-review__engine {
  width: 10rem;
}

.ocr-review__stage {
  margin: 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
}

.ocr-review__body {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: var(--yg-space-3);
  align-items: start;
}

.ocr-review__pane {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-2);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
}

.ocr-review__pane-title {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-weight: var(--yg-font-weight-medium);
}

.ocr-review__count {
  color: var(--yg-text-secondary);
  font-variant-numeric: tabular-nums;
}

.ocr-canvas {
  position: relative;
  max-height: 26rem;
  overflow: auto;
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-raised);
}

.ocr-canvas__img {
  display: block;
  width: 100%;
  height: auto;
}

.ocr-canvas__frame {
  position: relative;
}

.ocr-canvas__overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.ocr-box {
  position: absolute;
  padding: 0;
  border: 2px solid var(--yg-color-primary);
  border-radius: 2px;
  background: var(--yg-color-primary-soft);
  cursor: pointer;
  pointer-events: auto;
}

/* 排除的行用虚线与低透明度：一眼能分清「要填」和「不要」 */
.ocr-box:not(.is-selected) {
  border-style: dashed;
  border-color: var(--yg-text-tertiary);
  background: transparent;
  opacity: 0.35;
}

.ocr-box:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ocr-line-list {
  display: flex;
  min-height: 4rem;
  max-height: 26rem;
  flex-direction: column;
  gap: var(--yg-space-1);
  overflow: auto;
}

.ocr-line {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.ocr-line.is-excluded {
  opacity: 0.5;
}

.ocr-line__input {
  flex: 1 1 auto;
  min-width: 0;
}

.ocr-line__low {
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
}

.ocr-review__preview {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding-top: var(--yg-space-3);
  border-top: 1px solid var(--yg-border-subtle);
}

.ocr-review__warnings {
  margin: 0;
  padding-left: var(--yg-space-4);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-sm);
}

.ocr-review__hint {
  margin: 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.ocr-review__warn {
  margin: 0;
  padding: var(--yg-space-2) var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-sm);
}

.ocr-review__table-wrap {
  max-height: 12rem;
  overflow: auto;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
}

.ocr-review__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--yg-font-size-sm);
}

.ocr-review__table th {
  position: sticky;
  top: 0;
  z-index: var(--yg-z-sticky);
  padding: var(--yg-space-2);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-tertiary);
  font-weight: var(--yg-font-weight-medium);
  text-align: left;
  white-space: nowrap;
}

.ocr-review__table td {
  padding: var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
  color: var(--yg-text-primary);
}

.ocr-review__table .is-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.toolbar-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-2) var(--yg-space-3);
}

.toolbar-spacer {
  flex: 1 1 var(--yg-space-6);
}

/* 撤回 / 重做：两个图标按钮并成一组 */
.history-group {
  display: flex;
  align-items: center;
  gap: var(--yg-space-1);
  margin-right: var(--yg-space-2);
}

/* 覆盖确认弹窗内容 */
.overwrite-desc {
  margin: 0 0 var(--yg-space-3);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-normal);
}

.overwrite-truncated {
  margin: 0 0 var(--yg-space-3);
  padding: var(--yg-space-2) var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.overwrite-table-wrap {
  max-height: 18rem;
  overflow: auto;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
}

.overwrite-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--yg-font-size-sm);
}

.overwrite-table th {
  position: sticky;
  top: 0;
  z-index: var(--yg-z-sticky);
  padding: var(--yg-space-2);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-tertiary);
  font-weight: var(--yg-font-weight-medium);
  text-align: left;
  white-space: nowrap;
}

.overwrite-table td {
  padding: var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
  color: var(--yg-text-primary);
}

.overwrite-table .is-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.overwrite-action {
  display: inline-block;
  padding: 0 var(--yg-space-2);
  border-radius: var(--yg-radius-xs);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  white-space: nowrap;
}

.overwrite-action.is-overwrite {
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
}

.overwrite-action.is-skip {
  color: var(--yg-text-tertiary);
  background: var(--yg-bg-surface-sunken);
}

.grid-card {
  overflow: hidden;
}

.grid-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--yg-space-4);
  padding: var(--yg-space-3) var(--yg-space-4);
  border-bottom: 1px solid var(--yg-border-subtle);
  flex-wrap: wrap;
}

.grid-title {
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
}

.grid-sub {
  margin-top: var(--yg-space-1);
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

.summary-strip {
  display: flex;
  gap: var(--yg-space-4);
  flex-wrap: wrap;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: var(--yg-line-height-tight);
}

.summary-item span {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

.summary-item strong {
  font-size: var(--yg-font-size-2xl);
  font-variant-numeric: tabular-nums;
}

.summary-item .ok {
  color: var(--yg-text-success);
}

.summary-item .warning {
  color: var(--yg-text-warning);
}

.summary-item .error {
  color: var(--yg-text-danger);
}

.summary-item .saved {
  color: var(--yg-text-link);
}

.grid-wrap {
  min-height: 15rem;
  max-height: calc(100vh - 22.5rem);
  overflow: auto;
}

.grid-footer {
  display: flex;
  justify-content: center;
  padding: var(--yg-space-4);
  border-top: 1px solid var(--yg-border-subtle);
  background: var(--yg-bg-surface);
}

.reg-grid {
  width: 100%;
  min-width: 70rem;
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--yg-font-size-sm);
  table-layout: fixed;
}

.reg-grid th {
  position: sticky;
  top: 0;
  z-index: var(--yg-z-sticky);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-tertiary);
  font-weight: var(--yg-font-weight-medium);
  text-align: left;
  padding: var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
  white-space: nowrap;
}

.reg-grid td {
  padding: var(--yg-space-1);
  border-bottom: 1px solid var(--yg-border-subtle);
  vertical-align: middle;
}

.reg-grid tbody tr:hover td {
  background: var(--yg-bg-hover);
}

.rownum-col,
.rownum {
  width: 2.625rem;
  text-align: center;
  color: var(--yg-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.id-col {
  width: 9.25rem;
}

.name-col {
  width: 8.25rem;
}

.class-col {
  width: 8.875rem;
}

.duration-col {
  width: 6rem;
}

.mode-col {
  width: 8.25rem;
}

.note-col {
  width: 13.75rem;
}

.status-col {
  width: 11.875rem;
}

.action-col {
  width: 4.75rem;
}

.cell-wrap {
  position: relative;
}

.cell-input {
  box-sizing: border-box;
  width: 100%;
  height: var(--yg-control-height-md);
  padding: 0 var(--yg-space-2);
  border: 1px solid transparent;
  border-radius: var(--yg-radius-md);
  background: transparent;
  color: var(--yg-text-primary);
  font: inherit;
  transition:
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    box-shadow var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.cell-input::placeholder {
  color: var(--yg-text-tertiary);
}

.cell-input:focus {
  outline: none;
  border-color: var(--yg-color-primary);
  background: var(--yg-bg-surface);
  box-shadow: var(--yg-focus-ring);
}

.cell-input.is-error {
  border-color: var(--yg-color-danger);
  background: var(--yg-bg-surface);
}

.cell-input.is-warning {
  border-color: var(--yg-color-warning);
  background: var(--yg-bg-surface);
}

.duration-input {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.cell-select {
  box-sizing: border-box;
  width: 100%;
  height: var(--yg-control-height-md);
  padding: 0 var(--yg-space-2);
  border: 1px solid transparent;
  border-radius: var(--yg-radius-md);
  background: transparent;
  color: var(--yg-text-primary);
  font: inherit;
  transition:
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    box-shadow var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.cell-select:focus {
  outline: none;
  border-color: var(--yg-color-primary);
  background: var(--yg-bg-surface);
  box-shadow: var(--yg-focus-ring);
}

.class-cell {
  padding: 0 var(--yg-space-2);
  color: var(--yg-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-cell {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-width: 0;
  padding: var(--yg-space-1);
}

.status-badges {
  display: flex;
  align-items: center;
  gap: var(--yg-space-1);
  flex-wrap: wrap;
  min-width: 0;
}

.mode-locked {
  display: flex;
  align-items: center;
  height: var(--yg-control-height-md);
  padding: 0 var(--yg-space-2);
  overflow: hidden;
}

.status-pill {
  align-self: flex-start;
  padding: 0 var(--yg-space-2);
  border-radius: var(--yg-radius-xs);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  white-space: nowrap;
}

.status-empty {
  color: var(--yg-text-tertiary);
  background: var(--yg-bg-surface-sunken);
}

.status-ok {
  color: var(--yg-text-success);
  background: var(--yg-color-success-soft);
}

.status-warning {
  color: var(--yg-text-warning);
  background: var(--yg-color-warning-soft);
}

.status-error {
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
}

.status-saved {
  color: var(--yg-text-link);
  background: var(--yg-color-primary-soft);
}

.status-msg {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 非阻断提示（其他模式已登记）：与错误/警告文字区同位置，只换配色。 */
.status-msg--warning {
  color: var(--yg-text-warning);
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0;
}

.candidate-menu {
  position: absolute;
  top: calc(100% + var(--yg-space-1));
  left: 0;
  z-index: var(--yg-z-popup);
  min-width: 16.25rem;
  max-height: 15rem;
  overflow: auto;
  padding: var(--yg-space-1);
  background: var(--yg-bg-surface-raised);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-lg);
  box-shadow: var(--yg-shadow-lg);
}

.candidate-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  padding: var(--yg-space-2);
  border: 0;
  border-radius: var(--yg-radius-md);
  background: transparent;
  color: var(--yg-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.candidate-item:hover,
.candidate-item:focus-visible {
  background: var(--yg-bg-surface-sunken);
  outline: none;
}

.candidate-item:focus-visible {
  box-shadow: var(--yg-focus-ring);
}

.candidate-name {
  font-weight: var(--yg-font-weight-semibold);
}

.candidate-meta {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
}

.row-error .status-msg {
  color: var(--yg-text-danger);
}

.row-warning .status-msg {
  color: var(--yg-text-warning);
}

.register-page :deep(.el-button:focus-visible) {
  box-shadow: var(--yg-focus-ring);
}

@media (max-width: 768px) {
  .cell-input,
  .cell-select {
    height: var(--yg-control-height-lg);
  }
}

@supports (corner-shape: squircle) {
  .reg-card,
  .cell-input,
  .cell-select,
  .candidate-menu,
  .candidate-item,
  .status-pill,
  .list-hint,
  .notice-item,
  .overwrite-table-wrap,
  .overwrite-action {
    corner-shape: squircle;
  }
}

@media (max-width: 900px) {
  .register-page {
    padding: var(--yg-space-3);
  }

  .activity-picker {
    min-width: 0;
    width: 100%;
  }

  .activity-select {
    width: 100%;
  }

  .grid-wrap {
    max-height: calc(100vh - 20rem);
  }

  .page-head {
    width: 100%;
  }

  .ocr-review__body {
    grid-template-columns: minmax(0, 1fr);
  }

  .ocr-canvas,
  .ocr-line-list {
    max-height: 18rem;
  }
}
</style>

<!--
  弹层外壳（提示气泡 / 覆盖确认弹窗）被传送到 body，scoped 样式够不着，
  这里用全局块补一层：面板必须是实色，禁止半透明与 backdrop-filter（准则第 6 章）。
-->
<style>
.vr-notice-popper.el-popper.is-light {
  background: var(--yg-bg-surface-raised);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-lg);
  box-shadow: var(--yg-shadow-lg);
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
}

.vr-notice-popper .el-popper__arrow::before {
  background: var(--yg-bg-surface-raised);
  border: 1px solid var(--yg-border-default);
}

.vr-overwrite-dialog.el-dialog {
  background: var(--yg-bg-surface-raised);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-xl);
  box-shadow: var(--yg-shadow-lg);
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
}

/* OCR 确认弹窗：宽 1100px 由 width 属性给（min() 收敛到窄屏），这里只管外壳材质 */
.vr-ocr-dialog.el-dialog {
  background: var(--yg-bg-surface-raised);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-xl);
  box-shadow: var(--yg-shadow-lg);
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
}
</style>

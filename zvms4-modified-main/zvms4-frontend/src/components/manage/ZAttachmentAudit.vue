<script setup lang="ts">
/**
 * 附件管理（admin）：核查学生上传的附件、裁决与删除。
 *
 * 列表层后端只回元数据（不 SELECT OCR 正文），所以点开某一行才拉审计详情——
 * 详情里带 OCR 文本、逐行坐标与 FILE_EVENTS 时间线。裁决与删除都是留痕动作，
 * 一律二次确认 + 明确反馈；任何失败走统一的 ElMessage 出口。
 *
 * 缩略图走 blob + IntersectionObserver 懒加载：图片端点要 Bearer，直接把
 * URL 塞进 <img> 会 401；一次性把整页缩略图拉完又太重，滚到哪张取哪张。
 *
 * 进页即验：附件审计是查看学生证件与身份材料的高危动作，进页面先换一张
 * 5 分钟的 verify 短令牌（``withTemporaryToken``）。读接口本身不带令牌，
 * 令牌只用来证明「是本人刚输入过密码」；裁决与删除各自再包一层同一道闸。
 * 平板（学海设备）还要先过设备闸：``ensureDeviceUnlock`` 不通过，路由级守卫
 * 会把列表、缩略图与详情全部挡成 403，所以顺序是先解锁设备、再做密码再认证。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElCard,
  ElDatePicker,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTooltip
} from 'element-plus'
import type { TableInstance } from 'element-plus'
import { Lock, Picture, Refresh, ScaleToOriginal, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import ZSelectPerson from '@/components/form/ZSelectPerson.vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import {
  decideAttachment,
  getAttachmentAudit,
  listAuditAttachments,
  type AttachmentDecision
} from '@/api/attachment/audit'
import { getBytes, getThumb } from '@/api/attachment/read'
import { deleteOne } from '@/api/attachment/manage'
import { cachedTemporaryToken, isTemporaryTokenCancelled, withTemporaryToken } from '@/plugins/short-token'
import { ensureDeviceUnlock } from '@/plugins/device-unlock'
import { useUserStore } from '@/stores/user'
import { normalizeOcrBox, type OcrBoxRect } from '@/utils/ocrBox'
import { formatDateTime } from '@/utils/dateTime'
import type {
  AttachmentAuditDetail,
  AttachmentAuditEvent,
  AttachmentAuditListItem
} from '@/../types'

const { t } = useI18n()

const userStore = useUserStore()

const { width, height } = useWindowSize()

/** 窄屏（≤768px）：列宽收窄、筛选栏折叠、抽屉占满。 */
const narrow = computed(() => width.value <= 768)

/** 抽屉宽度：窄屏占满，桌面留出「原图 + 文字」并排的空间。 */
const drawerSize = computed(() => (width.value < 768 ? '100%' : '56%'))

// ---------------------------------------------------------------------------
// 表格高度：视口高度 − 表格实际顶部 − 底部预留（与 ZDeviceBans 同一算法）
// ---------------------------------------------------------------------------

const tableRef = ref<TableInstance>()

const BOTTOM_ROOM = 96

function computeTableHeight(table: TableInstance | undefined): number {
  const el = (table as { $el?: HTMLElement } | undefined)?.$el
  const top = el?.getBoundingClientRect?.().top ?? 0
  if (top > 0 && top < height.value * 0.8) {
    return Math.max(280, Math.round(height.value - top - BOTTOM_ROOM))
  }
  return Math.max(280, Math.round(height.value - 320))
}

const tableHeight = ref(280)

function measureTable() {
  tableHeight.value = computeTableHeight(tableRef.value)
}

watch([width, height], () => {
  measureTable()
})

// ---------------------------------------------------------------------------
// 进页验证（密码再认证）
// ---------------------------------------------------------------------------

/** 后端 ``require_password_verification`` 的固定 detail：403 只表示缺一次复核。 */
const PASSWORD_VERIFICATION_DETAIL = '需要密码验证后才能继续'

/** 已拿到 5 分钟内的 verify 短令牌；false 时整页只渲染「需要密码验证」占位态。 */
const verified = ref(false)
/** 密码框正在等用户输入：按钮进 loading，避免重复触发第二条弹框。 */
const verifying = ref(false)

/**
 * 设备解锁（平板）状态。
 *
 * 非学海设备 ``ensureDeviceUnlock`` 直接放行，这里恒为 true；平板没解锁时请求头
 * 缺 ``X-Device-Unlock``，附件路由会整条 403，所以必须先解锁再拉数据。
 */
const deviceUnlocked = ref(false)
/** 解锁被取消：占位态补一句「先解锁设备」，否则用户会以为是密码本身错了。 */
const deviceUnlockCancelled = ref(false)

async function ensureUnlocked(): Promise<boolean> {
  if (deviceUnlocked.value) {
    return true
  }
  try {
    await ensureDeviceUnlock({ reason: t('manage.attachments.deviceUnlockReason') })
    deviceUnlocked.value = true
    deviceUnlockCancelled.value = false
    return true
  } catch {
    deviceUnlockCancelled.value = true
    return false
  }
}

function verificationUser(): string {
  return String(userStore._id ?? '')
}

/**
 * 判断这次失败是不是「复核令牌不被接受」。
 *
 * 401 会被 axios 拦截器当成登录过期并清会话，所以后端一律 403；但 403 也可能是
 * 「权限不足」「附件已裁决」，只有 detail 是密码闸那条才该退回验证占位态。
 */
function isPasswordVerificationRejected(error: unknown): boolean {
  const response = (
    error as { response?: { status?: number; data?: { detail?: unknown } } } | undefined
  )?.response
  return response?.status === 403 && response.data?.detail === PASSWORD_VERIFICATION_DETAIL
}

/**
 * 取（或复用）verify 短令牌。缓存里有就直接放行；没有才弹一次密码框。
 *
 * 用户取消 / 密码错误都只停在占位态——**不**自动重弹，否则取消也会被下一次
 * 挂载或刷新再次挡住，变成关不掉的弹框风暴。
 */
async function unlock(): Promise<void> {
  if (verifying.value) {
    return
  }
  const userid = verificationUser()
  if (!userid) {
    verified.value = false
    return
  }
  if (cachedTemporaryToken(userid)) {
    verified.value = true
    return
  }
  verifying.value = true
  try {
    await withTemporaryToken(userid, t('manage.attachments.verifyReason'), async () => true)
    verified.value = true
  } catch {
    // 取消 / 密码错误 / 网络失败：拦截器已经提示过，这里只留在占位态等用户重试。
    verified.value = false
  } finally {
    verifying.value = false
  }
}

/** 占位态的「重新验证」与进页共用：先解锁设备，再拿到令牌才拉列表。 */
async function verifyAndLoad(): Promise<void> {
  if (!(await ensureUnlocked())) {
    ElMessage.warning(t('manage.attachments.deviceUnlockRequired'))
    return
  }
  await unlock()
  if (verified.value) {
    void load()
  }
}

/**
 * 写操作失败出口：密码框被取消 → 静默回占位态；令牌仍被拒 → 回占位态等用户
 * 重新验证；其余失败沿用 ``reportError`` 的统一提示。
 */
function handleWriteFailure(error: unknown, fallbackKey: string) {
  if (isTemporaryTokenCancelled(error)) {
    verified.value = false
    return
  }
  if (isPasswordVerificationRejected(error)) {
    verified.value = false
  }
  reportError(error, fallbackKey)
}

// ---------------------------------------------------------------------------
// 筛选与列表
// ---------------------------------------------------------------------------

/** 用途是开放集合：后端没有枚举接口，候选只是已知常见值，允许手填。 */
const PURPOSE_OPTIONS = ['volunteer_order']
const STATUS_OPTIONS = [
  'stored',
  'processing',
  'ready',
  'verified',
  'rejected',
  'quarantined',
  'deleted'
]
const OCR_STATUS_OPTIONS = ['pending', 'processing', 'done', 'failed']
const KIND_OPTIONS = ['original', 'thumb', 'derived']

const rows = ref<AttachmentAuditListItem[]>([])
const total = ref(0)
const page = ref(1)
const perpage = ref(20)
const loading = ref(false)
const loadFailed = ref(false)

const filters = reactive({
  purpose: '',
  status: '',
  /**
   * 默认只看原图：缩略图与派生文件在 FILES 表里是原图的附属行，
   * 不挡掉的话同一个上传会在列表里出现两次（名字与 mime 还一模一样）。
   */
  kind: 'original',
  ocrStatus: '',
  ownerOid: '',
  /** 自定义上传时间范围；有值时 created_from/created_to 才带上。 */
  range: undefined as [Date, Date] | undefined
})

/** 窄屏下筛选栏默认收起，留给表格更多高度。 */
const filtersOpen = ref(false)

/** 过滤条件变动后回到第一页：签名串只在取值真的变了时才变。 */
const filterSignature = computed(() =>
  [
    filters.purpose,
    filters.status,
    filters.kind,
    filters.ocrStatus,
    filters.ownerOid,
    filters.range?.[0]?.getTime() ?? 0,
    filters.range?.[1]?.getTime() ?? 0
  ].join('|')
)

/** 依次递增的请求序号：慢响应回来时若已有更新的请求，直接丢弃。 */
let listSeq = 0

/**
 * 统一错误出口。
 *
 * axios 拦截器已经为 HTTP 错误弹过一条 toast，这里不再重复弹——每次失败用户
 * 只该看到一条提示；只有 HTTP 之外的失败（信封 status=error、解析异常）才补。
 */
function reportError(error: unknown, fallbackKey: string) {
  const status = (error as { response?: { status?: unknown } })?.response?.status
  if (typeof status === 'number') {
    return
  }
  ElMessage.error(t(fallbackKey))
}

async function load() {
  if (!verified.value) {
    return
  }
  const seq = ++listSeq
  loading.value = true
  try {
    const result = await listAuditAttachments({
      page: page.value,
      size: perpage.value,
      purpose: filters.purpose || undefined,
      status: filters.status || undefined,
      kind: filters.kind || undefined,
      ocrStatus: filters.ocrStatus || undefined,
      ownerOid: filters.ownerOid || undefined,
      createdFrom: filters.range?.[0]?.toISOString(),
      createdTo: filters.range?.[1]?.toISOString()
    })
    if (seq !== listSeq) {
      return
    }
    rows.value = result.data
    total.value = result.total
    loadFailed.value = false
    pruneThumbs()
  } catch (error) {
    if (seq !== listSeq) {
      return
    }
    rows.value = []
    total.value = 0
    loadFailed.value = true
    reportError(error, 'manage.attachments.loadFailed')
  } finally {
    if (seq === listSeq) {
      loading.value = false
    }
  }
}

function onOwnerChange(value: string | string[]) {
  filters.ownerOid = typeof value === 'string' ? value : ''
}

function resetFilters() {
  filters.purpose = ''
  filters.status = ''
  filters.kind = 'original'
  filters.ocrStatus = ''
  filters.ownerOid = ''
  filters.range = undefined
}

watch(filterSignature, () => {
  if (page.value === 1) {
    void load()
  } else {
    page.value = 1
  }
})

watch(page, () => {
  void load()
})

watch(perpage, () => {
  if (page.value === 1) {
    void load()
  } else {
    page.value = 1
  }
})

const emptyDescription = computed(() =>
  loadFailed.value ? t('manage.attachments.loadFailed') : t('manage.attachments.empty')
)

// ---------------------------------------------------------------------------
// 展示辅助
// ---------------------------------------------------------------------------

const STATUS_TAG_TYPE: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  stored: 'info',
  processing: 'warning',
  ready: 'primary',
  verified: 'success',
  rejected: 'danger',
  quarantined: 'warning',
  deleted: 'info'
}

const OCR_STATUS_TAG_TYPE: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  pending: 'info',
  processing: 'warning',
  done: 'success',
  failed: 'danger'
}

function statusType(status?: string) {
  return STATUS_TAG_TYPE[status ?? ''] ?? 'info'
}

function ocrStatusType(status?: string) {
  return OCR_STATUS_TAG_TYPE[status ?? ''] ?? 'info'
}

function statusLabel(status?: string): string {
  if (!status) {
    return '—'
  }
  const key = `manage.attachments.status.${status}`
  const label = t(key)
  return label === key ? status : label
}

function ocrStatusLabel(status?: string): string {
  if (!status) {
    return '—'
  }
  const key = `manage.attachments.ocrStatus.${status}`
  const label = t(key)
  return label === key ? status : label
}

function purposeLabel(purpose?: string): string {
  if (!purpose) {
    return '—'
  }
  const key = `manage.attachments.purpose.${purpose}`
  const label = t(key)
  return label === key ? purpose : label
}

function kindLabel(kind?: string): string {
  if (!kind) {
    return '—'
  }
  const key = `manage.attachments.kind.${kind}`
  const label = t(key)
  return label === key ? kind : label
}

/** oid 太长，表格里只留尾部 6 位；完整值放在 tooltip 里。 */
function shortOid(oid?: string): string {
  if (!oid) {
    return '—'
  }
  return oid.length > 8 ? `…${oid.slice(-6)}` : oid
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) {
    return '—'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

function dimensionsOf(row: AttachmentAuditListItem): string {
  if (!row.width || !row.height) {
    return ''
  }
  return ` · ${row.width}×${row.height}`
}

// ---------------------------------------------------------------------------
// 缩略图懒加载：IntersectionObserver 命中才去取 blob
// ---------------------------------------------------------------------------

type ThumbState = 'loading' | 'ready' | 'error'

const thumbState = reactive<Record<string, ThumbState>>({})
const thumbUrls = new Map<string, string>()
const thumbNodes = new Map<string, HTMLElement>()
let thumbObserver: IntersectionObserver | null = null

function createObjectUrl(blob: Blob): string {
  // jsdom 与部分旧浏览器没有 createObjectURL：拿不到就当加载失败，不抛错。
  if (typeof URL.createObjectURL !== 'function') {
    return ''
  }
  return URL.createObjectURL(blob)
}

function revokeObjectUrl(url: string) {
  if (url && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(url)
  }
}

function isThumbable(oid: string): boolean {
  const row = rows.value.find((item) => item._id === oid)
  if (!row) {
    return false
  }
  if (row.kind && row.kind !== 'original') {
    return false
  }
  return !row.mime || row.mime.startsWith('image/')
}

function loadThumb(oid: string) {
  if (thumbState[oid] === 'loading' || thumbState[oid] === 'ready') {
    return
  }
  thumbState[oid] = 'loading'
  getThumb(oid)
    .then((blob) => {
      const url = createObjectUrl(blob)
      if (!url) {
        thumbState[oid] = 'error'
        return
      }
      thumbUrls.set(oid, url)
      thumbState[oid] = 'ready'
    })
    .catch(() => {
      thumbState[oid] = 'error'
    })
}

function ensureThumbObserver() {
  if (thumbObserver || typeof IntersectionObserver === 'undefined') {
    return
  }
  thumbObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue
        }
        const oid = (entry.target as HTMLElement).dataset?.oid ?? ''
        if (!oid) {
          continue
        }
        thumbObserver?.unobserve(entry.target)
        loadThumb(oid)
      }
    },
    { rootMargin: '160px' }
  )
}

/** 单元格挂载时登记：滚进视口才去取缩略图。 */
function registerThumb(el: unknown, oid: string) {
  const node = (el ?? null) as HTMLElement | null
  const known = thumbNodes.get(oid)
  if (known === node) {
    return
  }
  if (known) {
    thumbObserver?.unobserve(known)
    thumbNodes.delete(oid)
  }
  if (!node) {
    return
  }
  thumbNodes.set(oid, node)
  if (!isThumbable(oid)) {
    return
  }
  ensureThumbObserver()
  if (thumbObserver) {
    thumbObserver.observe(node)
  } else {
    void loadThumb(oid)
  }
}

function thumbUrl(oid: string): string {
  return thumbUrls.get(oid) ?? ''
}

function thumbFailed(oid: string): boolean {
  return thumbState[oid] === 'error'
}

function releaseThumbs() {
  thumbObserver?.disconnect()
  thumbObserver = null
  thumbNodes.clear()
  for (const url of thumbUrls.values()) {
    revokeObjectUrl(url)
  }
  thumbUrls.clear()
}

/**
 * 翻页/改筛选后清掉不在当前列表里的缩略图：blob URL 不回收就是内存泄漏，
 * 一次审计翻几十页能攒出上百个 56px 的图。
 */
function pruneThumbs() {
  const alive = new Set(rows.value.map((row) => row._id))
  for (const [oid, url] of [...thumbUrls.entries()]) {
    if (alive.has(oid)) {
      continue
    }
    revokeObjectUrl(url)
    thumbUrls.delete(oid)
    delete thumbState[oid]
  }
  for (const oid of [...thumbNodes.keys()]) {
    if (!alive.has(oid)) {
      thumbNodes.delete(oid)
    }
  }
}

// ---------------------------------------------------------------------------
// 详情抽屉
// ---------------------------------------------------------------------------

const drawerOpen = ref(false)
const detail = ref<AttachmentAuditDetail | null>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)
const activeOid = ref('')
const deciding = ref(false)
const decisionNote = ref('')
/** 从「裁决」入口打开时高亮底部裁决条，并把光标放进备注框。 */
const decideFocus = ref(false)
const noteInputRef = ref<{ focus?: () => void } | null>(null)

let detailSeq = 0

const detailTitle = computed(() => {
  const name = detail.value?.original_name || activeOid.value
  return name ? `${t('manage.attachments.detail.title')} · ${name}` : t('manage.attachments.detail.title')
})

async function loadDetail(oid: string) {
  const seq = ++detailSeq
  detailLoading.value = true
  detailFailed.value = false
  try {
    const result = await getAttachmentAudit(oid)
    if (seq !== detailSeq) {
      return
    }
    detail.value = result
  } catch (error) {
    if (seq !== detailSeq) {
      return
    }
    detail.value = null
    detailFailed.value = true
    reportError(error, 'manage.attachments.detail.loadFailed')
  } finally {
    if (seq === detailSeq) {
      detailLoading.value = false
    }
  }
}

function openDetail(row: AttachmentAuditListItem, focusDecision = false) {
  activeOid.value = row._id
  detail.value = null
  decisionNote.value = ''
  decideFocus.value = focusDecision
  drawerOpen.value = true
  void loadDetail(row._id)
  if (focusDecision) {
    // 抽屉是异步渲染的，等它出来再把光标放进备注框。
    void nextTick(() => noteInputRef.value?.focus?.())
  }
}

function retryDetail() {
  if (activeOid.value) {
    void loadDetail(activeOid.value)
  }
}

// ---------------------------------------------------------------------------
// 原图查看：缩放 / 拖拽
// ---------------------------------------------------------------------------

const originalUrl = ref('')
const originalFailed = ref(false)
const imageSize = reactive({ width: 0, height: 0 })
const imageZoom = ref(1)
const imageOffset = reactive({ x: 0, y: 0 })
const dragging = ref(false)
const activeLineIndex = ref(-1)

let dragOrigin = { x: 0, y: 0, ox: 0, oy: 0 }
let dragMoved = false

function releaseOriginal() {
  revokeObjectUrl(originalUrl.value)
  originalUrl.value = ''
  originalFailed.value = false
  imageSize.width = 0
  imageSize.height = 0
  imageZoom.value = 1
  imageOffset.x = 0
  imageOffset.y = 0
  activeLineIndex.value = -1
}

async function loadOriginal(oid: string) {
  releaseOriginal()
  if (!oid) {
    originalFailed.value = true
    return
  }
  try {
    const blob = await getBytes(oid)
    if (activeOid.value !== oid) {
      return
    }
    const url = createObjectUrl(blob)
    if (!url) {
      originalFailed.value = true
      return
    }
    originalUrl.value = url
  } catch {
    if (activeOid.value === oid) {
      originalFailed.value = true
    }
  }
}

function onOriginalLoad(event: Event) {
  const image = event.target as HTMLImageElement
  imageSize.width = image.naturalWidth || detail.value?.width || 0
  imageSize.height = image.naturalHeight || detail.value?.height || 0
}

const zoomStyle = computed(() => ({
  transform: `translate3d(${imageOffset.x}px, ${imageOffset.y}px, 0) scale(${imageZoom.value})`
}))

function zoomBy(delta: number) {
  const next = Math.min(4, Math.max(0.5, Math.round((imageZoom.value + delta) * 100) / 100))
  imageZoom.value = next
  if (next <= 1) {
    imageOffset.x = 0
    imageOffset.y = 0
  }
}

function resetZoom() {
  imageZoom.value = 1
  imageOffset.x = 0
  imageOffset.y = 0
}

function onStageWheel(event: WheelEvent) {
  if (!originalUrl.value) {
    return
  }
  zoomBy(event.deltaY < 0 ? 0.25 : -0.25)
}

function onStagePointerDown(event: PointerEvent) {
  if (!originalUrl.value || imageZoom.value <= 1) {
    return
  }
  dragging.value = true
  dragMoved = false
  dragOrigin = { x: event.clientX, y: event.clientY, ox: imageOffset.x, oy: imageOffset.y }
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
}

function onStagePointerMove(event: PointerEvent) {
  if (!dragging.value) {
    return
  }
  const dx = event.clientX - dragOrigin.x
  const dy = event.clientY - dragOrigin.y
  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
    dragMoved = true
  }
  imageOffset.x = dragOrigin.ox + dx
  imageOffset.y = dragOrigin.oy + dy
}

function onStagePointerUp() {
  dragging.value = false
}

/** 单击切换放大 / 还原；拖拽过就不算点击。 */
function onStageClick() {
  if (dragMoved) {
    dragMoved = false
    return
  }
  if (imageZoom.value > 1) {
    resetZoom()
    return
  }
  zoomBy(1)
}

// ---------------------------------------------------------------------------
// OCR 文本与行坐标
// ---------------------------------------------------------------------------

interface AuditLine {
  key: string
  text: string
  confidence: number | null
  box: OcrBoxRect | null
}

const ocrLines = computed<AuditLine[]>(() => {
  const lines = detail.value?.lines ?? []
  return lines.map((line, index) => ({
    key: `${index}-${line.text ?? ''}`,
    text: line.text ?? '',
    confidence: line.confidence ?? null,
    box: normalizeOcrBox(line.box)
  }))
})

const ocrText = computed(() => detail.value?.ocr_text ?? '')

const events = computed<AttachmentAuditEvent[]>(() => detail.value?.events ?? [])

const ownerName = computed(() => {
  const value = detail.value
  if (!value) {
    return ''
  }
  return value.owner_name || value.owner?.name || ''
})

/** 学号：owner_id 就是后端取的 student_id（用户表里的 id 列）。 */
const ownerStudentId = computed(() => {
  const value = detail.value
  if (!value) {
    return ''
  }
  return value.owner_id || value.owner?.student_id || ''
})

/** 坐标按原图尺寸换算成百分比：图片缩放后框仍然贴在字上。 */
function boxStyle(box: OcrBoxRect): Record<string, string> {
  const { width: boxWidth, height: boxHeight } = imageSize
  if (!boxWidth || !boxHeight) {
    return {}
  }
  const left = Math.max(0, Math.min(box.left, boxWidth))
  const top = Math.max(0, Math.min(box.top, boxHeight))
  const right = Math.min(boxWidth, Math.max(box.left + box.width, 0))
  const bottom = Math.min(boxHeight, Math.max(box.top + box.height, 0))
  return {
    left: `${(left / boxWidth) * 100}%`,
    top: `${(top / boxHeight) * 100}%`,
    width: `${(Math.max(0, right - left) / boxWidth) * 100}%`,
    height: `${(Math.max(0, bottom - top) / boxHeight) * 100}%`
  }
}

function boxLabel(box: OcrBoxRect | null): string {
  if (!box) {
    return ''
  }
  return `${Math.round(box.left)},${Math.round(box.top)} · ${Math.round(box.width)}×${Math.round(box.height)}`
}

function eventLabel(event: AttachmentAuditEvent): string {
  const action = event.action ?? ''
  const key = `manage.attachments.eventActions.${action}`
  const label = t(key)
  const base = label === key ? action || '—' : label
  if (event.from_status && event.to_status) {
    return `${base} · ${statusLabel(event.from_status)} → ${statusLabel(event.to_status)}`
  }
  return base
}

/**
 * 事件备注：裁决备注落在 FILE_EVENTS.detail 里（JSON 列，可能是字符串也可能是对象）。
 * 空备注不占位，时间线才是干净的。
 */
function eventNote(event: AttachmentAuditEvent): string {
  const detail = event.detail
  if (typeof detail === 'string') {
    return detail.trim()
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    const note = record.note ?? record.entity_oid
    return typeof note === 'string' ? note.trim() : ''
  }
  return ''
}

// ---------------------------------------------------------------------------
// 裁决与删除
// ---------------------------------------------------------------------------

async function decide(status: AttachmentDecision) {
  const oid = activeOid.value
  if (!oid || deciding.value) {
    return
  }
  if (status === 'rejected') {
    try {
      await ElMessageBox.confirm(
        t('manage.attachments.detail.rejectConfirm'),
        t('manage.attachments.detail.rejectConfirmTitle'),
        {
          type: 'warning',
          confirmButtonText: t('manage.attachments.detail.reject'),
          cancelButtonText: t('manage.attachments.cancel'),
          confirmButtonClass: 'el-button--danger',
          closeOnClickModal: false
        }
      )
    } catch {
      return
    }
  }
  deciding.value = true
  try {
    await withTemporaryToken(
      verificationUser(),
      t('manage.attachments.verifyReason'),
      (token) =>
        decideAttachment(
          oid,
          { status, note: decisionNote.value.trim() || undefined },
          token
        )
    )
    ElMessage.success(
      t(status === 'verified' ? 'manage.attachments.detail.approved' : 'manage.attachments.detail.rejected')
    )
    decisionNote.value = ''
    void loadDetail(oid)
    void load()
  } catch (error) {
    handleWriteFailure(error, 'manage.attachments.detail.decideFailed')
  } finally {
    deciding.value = false
  }
}

async function confirmDelete(row: AttachmentAuditListItem) {
  const name = row.original_name || row._id
  try {
    await ElMessageBox.confirm(
      t('manage.attachments.delete.confirm', { name }),
      t('manage.attachments.delete.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.attachments.delete.confirmButton'),
        cancelButtonText: t('manage.attachments.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await withTemporaryToken(
      verificationUser(),
      t('manage.attachments.verifyReason'),
      (token) => deleteOne(row._id, token)
    )
    ElMessage.success(t('manage.attachments.delete.deleted'))
    void load()
  } catch (error) {
    handleWriteFailure(error, 'manage.attachments.delete.failed')
  }
}

onMounted(() => {
  void loadOriginal(activeOid.value)
  // 进页即验：平板先解锁设备，缓存里有 5 分钟内的令牌则直接加载，否则弹密码框。
  void verifyAndLoad()
})

watch(activeOid, (oid) => {
  void loadOriginal(oid)
})

onBeforeUnmount(() => {
  releaseThumbs()
  releaseOriginal()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 未验证：不留白页，给一个可重试的占位态 -->
    <ElCard v-if="!verified" shadow="never">
      <div v-loading="verifying" class="attachment-audit__locked">
        <ElIcon class="attachment-audit__locked-icon"><Lock /></ElIcon>
        <p class="attachment-audit__locked-title">
          {{ t('manage.attachments.verifyRequired') }}
        </p>
        <p class="attachment-audit__hint">{{ t('manage.attachments.verifyIntro') }}</p>
        <p v-if="deviceUnlockCancelled" class="attachment-audit__hint" data-testid="device-unlock-hint">
          {{ t('manage.attachments.deviceUnlockRequired') }}
        </p>
        <ElButton type="primary" :loading="verifying" @click="verifyAndLoad">
          {{ t('manage.attachments.verifyRetry') }}
        </ElButton>
      </div>
    </ElCard>

    <!-- 筛选栏 -->
    <ElCard v-if="verified" shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex min-w-0 flex-col gap-1">
            <span>{{ t('manage.attachments.title') }}</span>
            <span class="attachment-audit__hint">{{ t('manage.attachments.subtitle') }}</span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton v-if="narrow" size="small" @click="filtersOpen = !filtersOpen">
              {{ t('manage.attachments.filters') }}
            </ElButton>
            <ElButton text size="small" @click="resetFilters">
              {{ t('manage.attachments.resetFilters') }}
            </ElButton>
            <ElButton text size="small" :loading="loading" @click="load">
              <ElIcon class="mr-1"><Refresh /></ElIcon>
              {{ t('manage.attachments.refresh') }}
            </ElButton>
          </div>
        </div>
      </template>

      <div v-show="!narrow || filtersOpen" class="attachment-audit__filters">
        <div class="attachment-audit__field">
          <span class="attachment-audit__label">{{ t('manage.attachments.filterFields.purpose') }}</span>
          <ElSelect
            v-model="filters.purpose"
            class="w-full"
            filterable
            allow-create
            default-first-option
            clearable
            :placeholder="t('manage.attachments.filterFields.purposeAll')"
          >
            <ElOption
              v-for="item in PURPOSE_OPTIONS"
              :key="item"
              :label="purposeLabel(item)"
              :value="item"
            />
          </ElSelect>
        </div>

        <div class="attachment-audit__field">
          <span class="attachment-audit__label">{{ t('manage.attachments.filterFields.status') }}</span>
          <ElSelect
            v-model="filters.status"
            class="w-full"
            clearable
            :placeholder="t('manage.attachments.filterFields.statusAll')"
          >
            <ElOption
              v-for="item in STATUS_OPTIONS"
              :key="item"
              :label="statusLabel(item)"
              :value="item"
            />
          </ElSelect>
        </div>

        <div class="attachment-audit__field">
          <span class="attachment-audit__label">
            {{ t('manage.attachments.filterFields.ocrStatus') }}
          </span>
          <ElSelect
            v-model="filters.ocrStatus"
            class="w-full"
            clearable
            :placeholder="t('manage.attachments.filterFields.ocrStatusAll')"
          >
            <ElOption
              v-for="item in OCR_STATUS_OPTIONS"
              :key="item"
              :label="ocrStatusLabel(item)"
              :value="item"
            />
          </ElSelect>
        </div>

        <div class="attachment-audit__field">
          <span class="attachment-audit__label">{{ t('manage.attachments.filterFields.kind') }}</span>
          <ElSelect
            v-model="filters.kind"
            class="w-full"
            clearable
            :placeholder="t('manage.attachments.filterFields.kindAll')"
          >
            <ElOption
              v-for="item in KIND_OPTIONS"
              :key="item"
              :label="kindLabel(item)"
              :value="item"
            />
          </ElSelect>
        </div>

        <div class="attachment-audit__field">
          <span class="attachment-audit__label">{{ t('manage.attachments.filterFields.owner') }}</span>
          <ZSelectPerson
            :model-value="filters.ownerOid"
            :filter-start="6"
            full-width
            clearable
            :placeholder="t('manage.attachments.filterFields.ownerAll')"
            @update:model-value="onOwnerChange"
          />
        </div>

        <div class="attachment-audit__field attachment-audit__field--range">
          <span class="attachment-audit__label">{{ t('manage.attachments.filterFields.range') }}</span>
          <ElDatePicker
            v-model="filters.range"
            type="datetimerange"
            class="w-full"
            :range-separator="t('manage.attachments.filterFields.rangeTo')"
            :start-placeholder="t('manage.attachments.filterFields.rangeStart')"
            :end-placeholder="t('manage.attachments.filterFields.rangeEnd')"
          />
        </div>
      </div>
      <p class="attachment-audit__hint attachment-audit__filter-hint">
        {{ t('manage.attachments.filterHint') }}
      </p>
    </ElCard>

    <!-- 列表 -->
    <ElCard v-if="verified" shadow="never">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <span>{{ t('manage.attachments.table.title') }}</span>
          <span class="attachment-audit__hint tabular-nums">
            {{ t('manage.attachments.table.count', { count: total }) }}
          </span>
        </div>
      </template>

      <ElTable
        ref="tableRef"
        v-loading="loading"
        :data="rows"
        row-key="_id"
        stripe
        :max-height="tableHeight"
      >
        <ElTableColumn :label="t('manage.attachments.columns.thumbnail')" width="84" align="center">
          <template #default="{ row }">
            <div
              class="attachment-audit__thumb"
              :data-oid="row._id"
              :ref="(el) => registerThumb(el, row._id)"
            >
              <img
                v-if="thumbUrl(row._id)"
                class="attachment-audit__thumb-img"
                :src="thumbUrl(row._id)"
                :alt="row.original_name || ''"
                @error="thumbState[row._id] = 'error'"
              />
              <ElIcon v-else-if="thumbFailed(row._id)" class="attachment-audit__thumb-icon">
                <Picture />
              </ElIcon>
              <span v-else class="attachment-audit__thumb-skeleton" />
            </div>
          </template>
        </ElTableColumn>

        <ElTableColumn
          :label="t('manage.attachments.columns.file')"
          :min-width="narrow ? 160 : 220"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <div class="flex flex-col">
              <span class="attachment-audit__name">{{ row.original_name || row._id }}</span>
              <span class="attachment-audit__hint tabular-nums">
                {{ row.mime || '—' }} · {{ formatSize(row.size_bytes) }}{{ dimensionsOf(row) }}
              </span>
            </div>
          </template>
        </ElTableColumn>

        <ElTableColumn :label="t('manage.attachments.columns.purpose')" :min-width="narrow ? 100 : 130">
          <template #default="{ row }">
            <ElTag size="small" type="info" effect="plain">{{ purposeLabel(row.purpose) }}</ElTag>
          </template>
        </ElTableColumn>

        <ElTableColumn
          :label="t('manage.attachments.columns.binding')"
          :min-width="narrow ? 110 : 150"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-if="row.entity_oid" class="attachment-audit__mono">
              {{ row.entity_type || '—' }} · {{ shortOid(row.entity_oid) }}
            </span>
            <span v-else class="attachment-audit__hint">{{ t('manage.attachments.table.unbound') }}</span>
          </template>
        </ElTableColumn>

        <ElTableColumn :label="t('manage.attachments.columns.status')" :min-width="narrow ? 130 : 160">
          <template #default="{ row }">
            <div class="flex flex-wrap items-center gap-1">
              <ElTag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</ElTag>
              <ElTag size="small" :type="ocrStatusType(row.ocr_status)" effect="plain">
                OCR {{ ocrStatusLabel(row.ocr_status) }}
              </ElTag>
            </div>
          </template>
        </ElTableColumn>

        <ElTableColumn :label="t('manage.attachments.columns.owner')" :min-width="narrow ? 90 : 120">
          <template #default="{ row }">
            <ElTooltip v-if="row.owner_oid" :content="row.owner_oid" placement="top">
              <span class="attachment-audit__mono">{{ shortOid(row.owner_oid) }}</span>
            </ElTooltip>
            <span v-else class="attachment-audit__hint">—</span>
          </template>
        </ElTableColumn>

        <ElTableColumn
          :label="t('manage.attachments.columns.createdAt')"
          :min-width="narrow ? 130 : 150"
        >
          <template #default="{ row }">
            <span class="attachment-audit__hint tabular-nums">
              {{ formatDateTime(row.created_at) }}
            </span>
          </template>
        </ElTableColumn>

        <ElTableColumn fixed="right" :width="narrow ? 150 : 180" align="right">
          <template #default="{ row }">
            <div class="flex items-center justify-end gap-1">
              <ElButton text type="primary" size="small" @click="openDetail(row)">
                {{ t('manage.attachments.table.view') }}
              </ElButton>
              <ElButton text type="warning" size="small" @click="openDetail(row, true)">
                {{ t('manage.attachments.table.decide') }}
              </ElButton>
              <ElButton text type="danger" size="small" @click="confirmDelete(row)">
                {{ t('manage.attachments.table.delete') }}
              </ElButton>
            </div>
          </template>
        </ElTableColumn>

        <template #empty>
          <ElEmpty :description="emptyDescription" :image-size="100" />
        </template>
      </ElTable>

      <ZPagination v-model:current-page="page" v-model:page-size="perpage" :total="total" />
    </ElCard>

    <!-- 详情抽屉 -->
    <ElDrawer v-if="verified" v-model="drawerOpen" direction="rtl" :size="drawerSize" :title="detailTitle">
      <div v-loading="detailLoading" class="attachment-audit__detail">
        <template v-if="detailFailed">
          <ElEmpty :description="t('manage.attachments.detail.loadFailed')" :image-size="90">
            <ElButton size="small" @click="retryDetail">
              {{ t('manage.attachments.detail.retry') }}
            </ElButton>
          </ElEmpty>
        </template>

        <div v-else class="attachment-audit__detail-grid">
          <!-- 左：原图 -->
          <section class="attachment-audit__viewer">
            <div class="attachment-audit__pane-title">
              {{ t('manage.attachments.detail.original') }}
            </div>
            <div
              class="attachment-audit__stage"
              :class="{ 'is-dragging': dragging }"
              @wheel.prevent="onStageWheel"
              @pointerdown="onStagePointerDown"
              @pointermove="onStagePointerMove"
              @pointerup="onStagePointerUp"
              @pointercancel="onStagePointerUp"
              @click="onStageClick"
            >
              <div v-if="originalUrl" class="attachment-audit__zoom" :style="zoomStyle">
                <img
                  class="attachment-audit__original"
                  :src="originalUrl"
                  :alt="detail?.original_name || ''"
                  @load="onOriginalLoad"
                />
                <div class="attachment-audit__overlay">
                  <button
                    v-for="(line, index) in ocrLines"
                    v-show="line.box"
                    :key="line.key"
                    type="button"
                    class="attachment-audit__box"
                    :class="{ 'is-active': activeLineIndex === index }"
                    :style="line.box ? boxStyle(line.box) : undefined"
                    :title="line.text"
                    :aria-label="line.text"
                    @click.stop="activeLineIndex = activeLineIndex === index ? -1 : index"
                  />
                </div>
              </div>
              <p v-else-if="originalFailed" class="attachment-audit__hint">
                {{ t('manage.attachments.detail.imageFailed') }}
              </p>
              <p v-else class="attachment-audit__hint">
                {{ t('manage.attachments.detail.imageLoading') }}
              </p>
            </div>
            <div class="attachment-audit__viewer-bar">
              <ElButton size="small" :disabled="!originalUrl" @click="zoomBy(-0.25)">
                <ElIcon><ZoomOut /></ElIcon>
              </ElButton>
              <span class="attachment-audit__hint tabular-nums">
                {{ Math.round(imageZoom * 100) }}%
              </span>
              <ElButton size="small" :disabled="!originalUrl" @click="zoomBy(0.25)">
                <ElIcon><ZoomIn /></ElIcon>
              </ElButton>
              <ElButton size="small" :disabled="!originalUrl" @click="resetZoom">
                <ElIcon><ScaleToOriginal /></ElIcon>
              </ElButton>
            </div>
          </section>

          <!-- 右：元数据 / OCR / 时间线 -->
          <section class="attachment-audit__side">
            <div class="attachment-audit__meta">
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.owner') }}
              </span>
              <span class="attachment-audit__meta-value">
                {{ ownerName || '—' }}
                <span v-if="ownerStudentId" class="attachment-audit__hint tabular-nums">
                  {{ ownerStudentId }}
                </span>
              </span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.mime') }}
              </span>
              <span class="attachment-audit__meta-value">{{ detail?.mime || '—' }}</span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.size') }}
              </span>
              <span class="attachment-audit__meta-value tabular-nums">
                {{ formatSize(detail?.size_bytes) }}
              </span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.dimensions') }}
              </span>
              <span class="attachment-audit__meta-value tabular-nums">
                {{ detail?.width && detail?.height ? `${detail.width}×${detail.height}` : '—' }}
              </span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.purpose') }}
              </span>
              <span class="attachment-audit__meta-value">{{ purposeLabel(detail?.purpose) }}</span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.status') }}
              </span>
              <span class="attachment-audit__meta-value">
                {{ statusLabel(detail?.status) }} · OCR {{ ocrStatusLabel(detail?.ocr_status) }}
              </span>
              <span class="attachment-audit__meta-label">
                {{ t('manage.attachments.detail.meta.createdAt') }}
              </span>
              <span class="attachment-audit__meta-value tabular-nums">
                {{ formatDateTime(detail?.created_at) }}
              </span>
            </div>

            <div class="attachment-audit__pane-title">
              {{ t('manage.attachments.detail.ocrText') }}
            </div>
            <pre v-if="ocrText" class="attachment-audit__ocr">{{ ocrText }}</pre>
            <p v-else class="attachment-audit__hint">{{ t('manage.attachments.detail.noOcr') }}</p>

            <div class="attachment-audit__pane-title">
              {{ t('manage.attachments.detail.lines') }}
              <span class="attachment-audit__hint tabular-nums">{{ ocrLines.length }}</span>
            </div>
            <ul v-if="ocrLines.length" class="attachment-audit__lines">
              <li
                v-for="(line, index) in ocrLines"
                :key="line.key"
                class="attachment-audit__line"
                :class="{ 'is-active': activeLineIndex === index }"
                @click="activeLineIndex = index"
              >
                <span class="attachment-audit__line-index tabular-nums">{{ index + 1 }}</span>
                <span class="attachment-audit__line-text">{{ line.text }}</span>
                <span class="attachment-audit__line-box tabular-nums">{{ boxLabel(line.box) }}</span>
              </li>
            </ul>
            <p v-else class="attachment-audit__hint">{{ t('manage.attachments.detail.noLines') }}</p>

            <div class="attachment-audit__pane-title">
              {{ t('manage.attachments.detail.events') }}
            </div>
            <ol v-if="events.length" class="attachment-audit__timeline">
              <li
                v-for="(event, index) in events"
                :key="event._id || index"
                class="attachment-audit__event"
              >
                <span class="attachment-audit__event-dot" aria-hidden="true" />
                <div class="attachment-audit__event-body">
                  <span class="attachment-audit__event-action">{{ eventLabel(event) }}</span>
                  <span class="attachment-audit__event-time tabular-nums">
                    {{ formatDateTime(event.created_at) }}
                  </span>
                  <p v-if="eventNote(event)" class="attachment-audit__event-note">
                    {{ eventNote(event) }}
                  </p>
                </div>
              </li>
            </ol>
            <p v-else class="attachment-audit__hint">{{ t('manage.attachments.detail.noEvents') }}</p>
          </section>
        </div>
      </div>

      <template #footer>
        <div class="attachment-audit__decide" :class="{ 'is-focus': decideFocus }">
          <ElInput
            ref="noteInputRef"
            v-model="decisionNote"
            maxlength="200"
            show-word-limit
            :disabled="deciding"
            :placeholder="t('manage.attachments.detail.notePlaceholder')"
          />
          <ElButton
            type="success"
            :loading="deciding"
            :disabled="detailFailed || !detail"
            @click="decide('verified')"
          >
            {{ t('manage.attachments.detail.approve') }}
          </ElButton>
          <ElButton
            type="danger"
            plain
            :loading="deciding"
            :disabled="detailFailed || !detail"
            @click="decide('rejected')"
          >
            {{ t('manage.attachments.detail.reject') }}
          </ElButton>
        </div>
      </template>
    </ElDrawer>
  </div>
</template>

<style scoped>
.attachment-audit__hint {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
}

/* 需要密码验证的占位态：卡片居中一张锁，不铺满整页留白 */
.attachment-audit__locked {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-8) var(--yg-space-4);
  text-align: center;
}

.attachment-audit__locked-icon {
  color: var(--yg-text-tertiary);
  font-size: 32px;
}

.attachment-audit__locked-title {
  margin: 0;
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
}

.attachment-audit__name {
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-sm);
  overflow-wrap: anywhere;
}

.attachment-audit__mono {
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
}

/* 筛选栅格：按可用宽度自动分列，窄屏退化成单列 */
.attachment-audit__filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  gap: var(--yg-space-3);
}

.attachment-audit__field {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-width: 0;
}

.attachment-audit__label {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

/* 缩略图：固定比例 + 骨架占位，图片没到之前不跳版 */
.attachment-audit__thumb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.attachment-audit__thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.attachment-audit__thumb-icon {
  color: var(--yg-text-tertiary);
  font-size: 20px;
}

.attachment-audit__thumb-skeleton {
  width: 100%;
  height: 100%;
  background: linear-gradient(
    90deg,
    var(--yg-bg-surface-sunken) 0%,
    var(--yg-bg-surface-raised) 50%,
    var(--yg-bg-surface-sunken) 100%
  );
}

/* 详情：左图右文，窄屏改成上下 */
.attachment-audit__detail {
  min-height: var(--yg-space-10);
}

.attachment-audit__detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: var(--yg-space-4);
  align-items: start;
}

.attachment-audit__viewer {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  min-width: 0;
}

.attachment-audit__pane-title {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-weight: var(--yg-font-weight-medium);
}

.attachment-audit__stage {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 16rem;
  max-height: 26rem;
  overflow: auto;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  cursor: zoom-in;
}

.attachment-audit__stage.is-dragging {
  cursor: grabbing;
}

/* 缩放作用在这一层：图与坐标框一起缩放，框不会跑偏 */
.attachment-audit__zoom {
  position: relative;
  display: inline-block;
  transform-origin: center center;
  transition: transform var(--yg-dur-base) var(--yg-ease-out);
}

.attachment-audit__original {
  display: block;
  max-width: 100%;
  height: auto;
}

.attachment-audit__overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.attachment-audit__box {
  position: absolute;
  padding: 0;
  border: 2px solid var(--yg-color-primary);
  border-radius: 2px;
  background: var(--yg-color-primary-soft);
  cursor: pointer;
  pointer-events: auto;
}

.attachment-audit__box.is-active {
  border-color: var(--yg-color-warning);
  background: var(--yg-color-warning-soft);
  box-shadow: 0 0 0 2px var(--yg-color-warning-soft);
}

.attachment-audit__viewer-bar {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.attachment-audit__side {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  min-width: 0;
}

.attachment-audit__meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--yg-space-2) var(--yg-space-4);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.attachment-audit__meta-label {
  color: var(--yg-text-tertiary);
}

.attachment-audit__meta-value {
  min-width: 0;
  color: var(--yg-text-primary);
  overflow-wrap: anywhere;
}

.attachment-audit__ocr {
  margin: 0;
  max-height: 14rem;
  overflow: auto;
  padding: var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-relaxed);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.attachment-audit__lines {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  max-height: 16rem;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.attachment-audit__line {
  display: grid;
  grid-template-columns: 1.6rem minmax(0, 1fr) max-content;
  align-items: baseline;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1) var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.attachment-audit__line:hover {
  background: var(--yg-bg-hover);
}

.attachment-audit__line.is-active {
  background: var(--yg-color-primary-soft);
  color: var(--yg-text-primary);
}

.attachment-audit__line-index {
  color: var(--yg-text-tertiary);
}

.attachment-audit__line-text {
  overflow-wrap: anywhere;
}

.attachment-audit__line-box {
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

/* 审计时间线：左侧一条竖线 + 圆点 */
.attachment-audit__timeline {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin: 0;
  padding: 0 0 0 var(--yg-space-3);
  list-style: none;
  border-left: 1px solid var(--yg-border-subtle);
}

.attachment-audit__event {
  display: flex;
  align-items: flex-start;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
}

.attachment-audit__event-dot {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  margin-left: calc(-1 * var(--yg-space-3) - 4px);
  border-radius: 50%;
  background: var(--yg-color-primary);
}

.attachment-audit__event-body {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--yg-space-2);
  min-width: 0;
}

.attachment-audit__event-action {
  color: var(--yg-text-primary);
}

.attachment-audit__event-time {
  color: var(--yg-text-tertiary);
}

.attachment-audit__event-note {
  flex: 1 1 100%;
  margin: 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  overflow-wrap: anywhere;
}

/* 裁决条：备注铺满，按钮靠右 */
.attachment-audit__decide {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1);
  border: 1px solid transparent;
  border-radius: var(--yg-radius-md);
  transition:
    border-color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out);
}

/* 从「裁决」入口进来时给裁决条一个焦点环：一眼知道该在这里动手 */
.attachment-audit__decide.is-focus {
  border-color: var(--yg-color-primary);
  background: var(--yg-color-primary-soft);
}

.attachment-audit__decide :deep(.el-input) {
  flex: 1 1 auto;
  min-width: 0;
}

@media (max-width: 768px) {
  .attachment-audit__detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .attachment-audit__decide {
    flex-wrap: wrap;
  }
}
</style>

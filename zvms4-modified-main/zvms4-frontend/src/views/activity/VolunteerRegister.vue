<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPageHeader,
  ElSelect,
  ElTag
} from 'element-plus'
import {
  CopyDocument,
  Delete,
  Download,
  Plus,
  Refresh,
  UploadFilled,
  View
} from '@element-plus/icons-vue'
import api from '@/api'
import ResultState from '@/ui/ux/ResultState.vue'
import { useHeaderStore } from '@/stores/header'
import { useUserStore } from '@/stores/user'
import type { ActivityMode } from '@/../types'
import type { Activity, NewActivityMember } from '@/../types/v2'
import {
  loadStudentIndex,
  matchStudentRow,
  normalizePersonName,
  normalizeStudentId,
  suggestStudents,
  syncStudentIndex,
  type StudentIndexState,
  type StudentRecord
} from '@/utils/studentIndex'
import {
  parseVolunteerSheet,
  type OcrVolunteerRow,
  type VolunteerOcrProgress
} from '@/api/volunteer'
import { aiErrorCodeOf, aiErrorCopy } from '@/api/ai/errors'

type RegistrationMode = Exclude<ActivityMode, 'hybrid'>
type RowStatus = 'empty' | 'ok' | 'warning' | 'error' | 'saved'

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
}

const DRAFT_KEY = 'volunteer-register-draft-v1'
const INDEX_STALE_MS = 24 * 60 * 60 * 1000
const MODES: RegistrationMode[] = ['on-campus', 'off-campus', 'social-practice']

const router = useRouter()
const headerStore = useHeaderStore()
const userStore = useUserStore()
const { t } = useI18n()

if (!(userStore.position.includes('admin') || userStore.position.includes('department'))) {
  void router.replace('/not-found')
}
headerStore.setHeader(t('nav.register'))

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

const rows = ref<RegistrationRow[]>([])
const activeCell = ref<{ r: number; c: number } | null>(null)
const submitLoading = ref(false)
/** 提交结果页：成功给对勾 + 完成，失败写原因 + 返回修改 */
const resultVisible = ref(false)
const submitOutcome = ref<{ ok: boolean; reason: string }>({ ok: true, reason: '' })
const ocrLoading = ref(false)
const ocrStage = ref('')
const savedActivityId = ref('')
const savedCount = ref(0)

let rowSeq = 0
let draftTimer: ReturnType<typeof setTimeout> | undefined
let draftReady = false
let savedOids = new Set<string>()

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
  for (const row of nonEmptyRows.value) {
    if (row.status === 'ok') ready += 1
    else if (row.status === 'saved') saved += 1
    else if (row.status === 'error') error += 1
    else warning += 1
  }
  return { total: nonEmptyRows.value.length, ready, warning, error, saved }
})

const canSubmit = computed(
  () =>
    Boolean(selectedActivityId.value) &&
    pendingRows.value.length > 0 &&
    stats.value.warning === 0 &&
    stats.value.error === 0
)

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
    ...partial
  }
}

function resetRows(count = 8) {
  rows.value = Array.from({ length: count }, () => createRow())
}

function ensureRows(count: number) {
  while (rows.value.length < count) rows.value.push(createRow())
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
  if (!Number.isFinite(value) || value <= 0 || value > 24) return null
  return value
}

function durationMessage(row: RegistrationRow): string {
  if (!row.duration.trim()) return '请填写时长'
  if (durationValue(row) === null) return '时长需为 0–24 之间的数字'
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

function revalidateRow(row: RegistrationRow) {
  if (!hasContent(row)) {
    row.status = 'empty'
    row.message = ''
    row.candidates = []
    row.oid = ''
    row.classId = ''
    row.className = ''
    return
  }

  const match = matchStudentRow(students.value, row.studentId, row.name)
  row.candidates = match.candidates

  if (match.record) {
    if (!row.studentId.trim()) row.studentId = match.record.studentId
    if (!row.name.trim()) row.name = match.record.name
    row.classId = match.record.classId
    row.className = match.record.className
    row.oid = match.record.oid
  } else {
    row.oid = ''
    row.classId = ''
    row.className = ''
  }

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
    status = 'error'
    message = match.message
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

  const activityType = selectedActivity.value?.type
  if (activityType && activityType !== 'hybrid') {
    row.mode = activityType as RegistrationMode
  }

  row.status = status
  row.message = message
}

function markDuplicates() {
  const byOid = new Map<string, RegistrationRow[]>()
  for (const row of rows.value) {
    if (!row.oid) continue
    const group = byOid.get(row.oid) ?? []
    group.push(row)
    byOid.set(row.oid, group)
  }
  for (const group of byOid.values()) {
    if (group.length < 2) continue
    for (const row of group) {
      row.status = 'error'
      row.message = row.message ? `${row.message}；名单内重复` : '名单内重复'
    }
  }
}

function revalidateAll() {
  for (const row of rows.value) revalidateRow(row)
  markDuplicates()
}

function onIdInput(row: RegistrationRow, event: Event) {
  row.studentId = normalizeStudentId((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onNameInput(row: RegistrationRow, event: Event) {
  row.name = normalizePersonName((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onDurationInput(row: RegistrationRow, event: Event) {
  row.duration = cleanDuration((event.target as HTMLInputElement).value)
  revalidateRow(row)
  markDuplicates()
}

function onModeChange(row: RegistrationRow) {
  revalidateRow(row)
  markDuplicates()
}

function applyCandidate(row: RegistrationRow, candidate: StudentRecord) {
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
  rows.value.push(createRow())
  void nextTick(() => focusCell(rows.value.length - 1, 0))
}

function duplicateRow(index: number) {
  const source = rows.value[index]
  if (!source) return
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

function removeRow(index: number) {
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
  return /^-?\d+(?:\.\d+)?$/.test(value.trim())
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
  ensureRows(start.r + data.length)

  data.forEach((cells, rowOffset) => {
    const row = rows.value[start.r + rowOffset]
    if (!row) return
    for (const [columnKey, cellIndex] of Object.entries(mapping)) {
      const column = Number(columnKey)
      writeCell(row, column, cells[cellIndex] ?? '')
    }
    revalidateRow(row)
  })
  markDuplicates()
  ElMessage.success(`已填入 ${data.length} 行，请复核匹配结果`)
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
  importInput.value?.click()
}

function triggerOcr() {
  ocrInput.value?.click()
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    if (file.name.toLowerCase().endsWith('.xlsx') || file.name.toLowerCase().endsWith('.xls')) {
      ElMessage.info('Excel 文件请先复制粘贴，或另存为 CSV 后导入')
      return
    }
    applyText(await file.text(), { r: 0, c: 0 })
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
    const result = await parseVolunteerSheet(file, (progress) => {
      ocrStage.value = describeOcrProgress(progress)
    })
    if (!result.rows.length) {
      ElMessage.warning(result.warnings?.join('；') || 'OCR 未解析出学生行，请手动粘贴')
      return
    }

    const start = rows.value.findIndex((row) => !hasContent(row))
    const base = start < 0 ? rows.value.length : start
    ensureRows(base + result.rows.length)
    result.rows.forEach((item, offset) => {
      const row = rows.value[base + offset]
      if (!row) return
      applyOcrRow(row, item)
      revalidateRow(row)
    })
    markDuplicates()

    if (result.warnings?.length) {
      ElMessage.warning(result.warnings.join('；'))
    }
    if (result.confidence !== undefined && result.confidence > 0 && result.confidence < 0.6) {
      ElMessage.warning('OCR 整体置信度较低，请逐行复核识别结果')
    }
    const engine = result.engine ? `（${result.engine}）` : ''
    ElMessage.success(`OCR 识别 ${result.rows.length} 行${engine}，已按本地名单校正`)
  } catch (error) {
    const code = aiErrorCodeOf(error)
    if (code) {
      const copy = aiErrorCopy(code, t)
      ElMessage.error(`${copy.title}：${copy.hint}`)
    } else {
      ElMessage.error((error as Error)?.message || 'OCR 识别失败，请稍后重试')
    }
  } finally {
    ocrLoading.value = false
    ocrStage.value = ''
    input.value = ''
  }
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
    // 只列已生效活动：pending/refused 的活动后端不拦，前端必须挡住
    const merged = result.activities.filter((activity) => activity.status === 'effective')
    if (
      selectedActivity.value &&
      selectedActivity.value.status === 'effective' &&
      !merged.some((activity) => activity._id === selectedActivity.value?._id)
    ) {
      merged.unshift(selectedActivity.value)
    }
    activityOptions.value = merged
  } catch {
    activityOptions.value =
      selectedActivity.value?.status === 'effective' ? [selectedActivity.value] : []
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
    selectedActivityId.value = ''
    selectedActivity.value = null
  }
}

async function onActivityChange() {
  await hydrateSelectedActivity()
  savedActivityId.value = ''
  savedCount.value = 0
  savedOids = new Set()
  const activity = selectedActivity.value
  if (activity && activity.type !== 'hybrid') {
    const mode = activity.type as RegistrationMode
    for (const row of rows.value) row.mode = mode
  }
  revalidateAll()
}

function viewSavedActivity() {
  if (savedActivityId.value) {
    void router.push(`/activity/details/${savedActivityId.value}`)
  }
}

async function submitRegistration() {
  revalidateAll()
  if (!selectedActivity.value) {
    ElMessage.warning('请先选择目标义工活动')
    return
  }
  if (!pendingRows.value.length) {
    ElMessage.info('没有新的待提交行；已提交的行如需修改，编辑后会自动重新进入待提交状态')
    return
  }
  const invalid = pendingRows.value.filter(
    (row) => row.status !== 'ok' || !row.oid || durationValue(row) === null
  )
  if (invalid.length) {
    ElMessage.error(`还有 ${invalid.length} 行需要修正后再提交`)
    return
  }
  // 后端 Body(max_length=1000)：超限整批 422，这里先拦并提示分批
  if (pendingRows.value.length > 1000) {
    ElMessage.error(`单次最多登记 1000 行，当前 ${pendingRows.value.length} 行，请分批提交`)
    return
  }

  const payload: NewActivityMember[] = pendingRows.value.map((row) => ({
    member: row.oid,
    mode: row.mode,
    duration: durationValue(row) as number
  }))

  submitLoading.value = true
  try {
    await api.activity.member.insertMany(selectedActivity.value._id, payload)
    savedActivityId.value = selectedActivity.value._id
    savedCount.value += payload.length
    for (const row of pendingRows.value) {
      row.status = 'saved'
      row.message = '已提交'
    }
    submitOutcome.value = {
      ok: true,
      reason: `已为《${selectedActivity.value.name}》登记 ${payload.length} 人。`
    }
    resultVisible.value = true
  } catch (error) {
    const detail =
      (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      (error as Error)?.message ||
      '提交失败，请重试'
    submitOutcome.value = { ok: false, reason: detail }
    resultVisible.value = true
  } finally {
    submitLoading.value = false
  }
}

function exportCsv() {
  const header = ['学号', '姓名', '班级', '时长', '模式', '备注', '匹配状态']
  const lines = rows.value
    .filter(hasContent)
    .map((row) =>
      [
        row.studentId,
        row.name,
        row.className || row.classId,
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

async function clearAll() {
  try {
    await ElMessageBox.confirm('清空后当前草稿不可恢复，确定继续？', '清空该登记明细？', {
      confirmButtonText: '清空',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
      type: 'warning'
    })
  } catch {
    return
  }
  localStorage.removeItem(DRAFT_KEY)
  selectedActivityId.value = ''
  selectedActivity.value = null
  savedActivityId.value = ''
  savedCount.value = 0
  savedOids = new Set()
  resetRows(8)
  revalidateAll()
}

function restoreDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) {
      resetRows(8)
      return
    }
    const parsed = JSON.parse(raw) as {
      rows?: Partial<RegistrationRow>[]
      activityId?: string
      savedActivityId?: string
      savedCount?: number
      savedOids?: string[]
    }
    rowSeq = 0
    rows.value = (parsed.rows ?? []).map((item) =>
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
    if (!rows.value.length) resetRows(8)
    selectedActivityId.value = String(parsed.activityId ?? '')
    savedActivityId.value = String(parsed.savedActivityId ?? '')
    savedCount.value = Number(parsed.savedCount) || 0
    savedOids = new Set(
      Array.isArray(parsed.savedOids)
        ? parsed.savedOids.filter((oid): oid is string => typeof oid === 'string')
        : []
    )
  } catch {
    resetRows(8)
  } finally {
    draftReady = true
    revalidateAll()
    if (savedActivityId.value && savedActivityId.value === selectedActivityId.value) {
      for (const row of rows.value) {
        if (row.oid && savedOids.has(row.oid)) {
          row.status = 'saved'
          row.message = '已提交'
        }
      }
    }
  }
}

function persistDraftNow() {
  if (!draftReady) return
  const payload = {
    activityId: selectedActivityId.value,
    savedActivityId: savedActivityId.value,
    savedCount: savedCount.value,
    savedOids: rows.value.filter((row) => row.status === 'saved' && row.oid).map((row) => row.oid),
    rows: rows.value.map((row) => ({
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
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(payload))
  } catch {
    // 隐私模式或配额不足时保持页面可用；草稿只是便利功能。
  }
}

function scheduleDraft() {
  if (!draftReady) return
  clearTimeout(draftTimer)
  draftTimer = setTimeout(persistDraftNow, 400)
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
    revalidateAll()
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
    revalidateAll()
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
  restoreDraft()
  await searchActivities('')
  await hydrateSelectedActivity()
  await initIndex()
}

watch(rows, scheduleDraft, { deep: true })
watch(selectedActivityId, scheduleDraft)

onMounted(() => {
  void initPage()
})

onBeforeUnmount(() => {
  clearTimeout(draftTimer)
  persistDraftNow()
})
</script>

<template>
  <div class="register-page yg2-page">
    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">批量登记</p>
        <h1 class="yg2-page-header__title">批量义工登记</h1>
        <p class="yg2-page-header__subtitle">
          粘贴、导入或 OCR 识别名单，逐行核对后一次性提交
        </p>
      </div>
      <div class="yg2-page-header__actions index-status">
        <span
          class="yg2-badge"
          :class="indexTagType === 'success' ? 'yg2-badge--accent' : 'yg2-badge--amber'"
        >{{ indexLabel }}</span>
        <span class="index-meta">
          {{ students.length }} 人
          <span v-if="indexUpdatedLabel"> · {{ indexUpdatedLabel }}</span>
        </span>
        <button
          class="yg2-btn yg2-btn--secondary yg2-btn--sm"
          type="button"
          :disabled="indexLoading || indexBooting"
          @click="refreshIndex()"
        >
          同步名单
        </button>
      </div>
    </header>

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
          <span class="muted">提交时写入该活动的成员时长</span>
        </div>
        <div v-else class="activity-summary muted">
          选择活动后才能提交；未选择时仍可编辑、粘贴和导出。
        </div>
      </section>

      <ElAlert
        v-if="!indexBooting && !indexLoading && !students.length"
        type="warning"
        show-icon
        :closable="false"
        title="本地学生名单未就绪"
        description="点击右上角“同步名单”拉取一次全校学生索引；之后补全只在本地 index.db 内完成，不会逐格查询数据库。"
      />

      <ElAlert
        v-if="selectedActivity && selectedActivity.type !== 'hybrid'"
        type="info"
        show-icon
        :closable="false"
        :title="`活动类型为${modeLabel(selectedActivity.type)}，明细行模式已锁定`"
      />

      <ElAlert
        type="info"
        show-icon
        :closable="false"
        title="OCR 识别会消耗 AI 网关额度"
        description="仅支持 JPG / PNG / WebP，单张不超过 15MB。识别文本会在本地拆行并用 index.db 校正，提交前请逐行复核。"
      />

      <section class="reg-card toolbar-card">
        <ElButton :icon="Plus" @click="addRow">新增行</ElButton>
        <ElButton :icon="CopyDocument" @click="pasteFromClipboard">从剪贴板粘贴</ElButton>
        <ElButton :icon="UploadFilled" @click="triggerImport">导入表格</ElButton>
        <ElButton :icon="UploadFilled" :loading="ocrLoading" @click="triggerOcr">
          OCR 识别
        </ElButton>
        <span v-if="ocrLoading && ocrStage" class="ocr-stage" role="status" aria-live="polite">
          {{ ocrStage }}
        </span>
        <ElButton :icon="Refresh" :loading="indexLoading || indexBooting" @click="refreshIndex()">
          同步名单
        </ElButton>
        <ElButton :icon="Download" @click="exportCsv">导出 CSV</ElButton>
        <ElButton v-if="savedActivityId" :icon="View" @click="viewSavedActivity">
          查看活动（{{ savedCount }} 人）
        </ElButton>
        <span class="toolbar-spacer" />
        <ElButton type="danger" plain @click="clearAll">清空</ElButton>
        <ElButton
          type="primary"
          :loading="submitLoading"
          :disabled="!canSubmit"
          @click="submitRegistration"
        >
          提交登记
        </ElButton>
        <input
          ref="importInput"
          type="file"
          accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values,text/plain"
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
            <div class="grid-title">登记明细</div>
            <div class="grid-sub">
              支持从 Excel 复制多行粘贴；学号/姓名会自动互相补全并标出同名或不一致
            </div>
          </div>
          <div class="summary-strip">
            <div class="summary-item">
              <span>总行数</span>
              <strong class="yg-text-num">{{ stats.total }}</strong>
            </div>
            <div class="summary-item">
              <span>待提交</span>
              <strong class="ok yg-text-num">{{ stats.ready }}</strong>
            </div>
            <div v-if="stats.saved" class="summary-item">
              <span>已提交</span>
              <strong class="saved yg-text-num">{{ stats.saved }}</strong>
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
                  <div class="class-cell" :title="row.className || row.classId">
                    {{ row.className || row.classId || '—' }}
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
                  <select
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
                    <span class="status-pill" :class="`status-${row.status}`">
                      {{ statusLabel(row.status) }}
                    </span>
                    <span v-if="row.message" class="status-msg" :title="row.message">
                      {{ row.message }}
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
      </section>
    </div>

    <ElDialog
      v-model="resultVisible"
      width="420px"
      align-center
      :show-close="false"
      :close-on-click-modal="false"
      class="yg2-result-dialog"
    >
      <ResultState
        :ok="submitOutcome.ok"
        :reason="submitOutcome.reason"
        next-text="完成"
        retry-text="返回修改"
        @next="resultVisible = false"
        @retry="resultVisible = false"
      />
    </ElDialog>
  </div>
</template>

<style scoped>
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

.muted {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

.ocr-stage {
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
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
  .status-pill {
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
}

/* v2 现代化覆盖 */
.reg-card {
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-20);
  box-shadow: var(--yg2-shadow-ambient);
}

.grid-head .grid-title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--yg2-text);
}

.grid-head .grid-sub {
  color: var(--yg2-text-secondary);
}

.reg-card :deep(.el-button) {
  height: 34px;
  border-radius: var(--yg2-r-pill);
  font-weight: 500;
}

.reg-card :deep(.el-select__wrapper) {
  border-radius: var(--yg2-r-12);
  min-height: 42px;
}
</style>

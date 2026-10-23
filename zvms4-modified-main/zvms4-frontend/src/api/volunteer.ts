import axios from '@/plugins/axios.ts'

/**
 * 志愿时长登记表的 OCR 识别接口（**预留**）。
 *
 * 后端目前没有该路由，页面需要自行捕获 404 / 501 并展示占位提示；
 * 这里只负责把文件发出去、把响应整形成稳定的行结构，绝不返回 undefined 的 rows。
 */
export const OCR_ENDPOINT = '/volunteer/registration/ocr'

export interface OcrVolunteerRow {
  student_id?: string
  studentId?: string
  name?: string
  duration?: number | string
  mode?: 'on-campus' | 'off-campus' | 'social-practice'
  note?: string
  confidence?: number
}

export interface OcrVolunteerResponse {
  rows: OcrVolunteerRow[]
  warnings?: string[]
}

const MODES = new Set<OcrVolunteerRow['mode']>(['on-campus', 'off-campus', 'social-practice'])

function text(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function durationOf(value: unknown): number | string | undefined {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : undefined
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return undefined
    const numeric = Number(trimmed)
    // 「2」「2.5」这类字符串统一成数字，其余（如「2小时」）保留原文交给页面判断。
    if (trimmed !== '' && Number.isFinite(numeric)) return numeric
    return trimmed
  }
  return undefined
}

function normalizeRow(raw: unknown): OcrVolunteerRow | null {
  if (!raw || typeof raw !== 'object') return null
  const source = raw as Record<string, unknown>

  const studentId = text(source.student_id ?? source.studentId)
  const name = text(source.name)
  // 既没有学号也没有姓名的行无法定位到人，视为脏数据丢弃。
  if (!studentId && !name) return null

  const row: OcrVolunteerRow = {}
  if (studentId) row.studentId = studentId
  if (name) row.name = name

  const duration = durationOf(source.duration)
  if (duration !== undefined) row.duration = duration

  if (typeof source.mode === 'string' && MODES.has(source.mode as OcrVolunteerRow['mode'])) {
    row.mode = source.mode as OcrVolunteerRow['mode']
  }

  const note = text(source.note)
  if (note) row.note = note

  if (typeof source.confidence === 'number' && Number.isFinite(source.confidence)) {
    row.confidence = source.confidence
  }

  return row
}

function rowsOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** 兼容 `{ rows }`、`{ data: [...] }`、`{ data: { rows } }` 与裸数组。 */
function extractPayload(payload: unknown): { rows: OcrVolunteerRow[]; warnings?: string[] } {
  if (Array.isArray(payload)) {
    return { rows: payload.map(normalizeRow).filter((row): row is OcrVolunteerRow => row !== null) }
  }
  if (!payload || typeof payload !== 'object') return { rows: [] }

  const envelope = payload as { rows?: unknown; data?: unknown; warnings?: unknown }
  let rawRows = rowsOf(envelope.rows)
  if (!rawRows.length) {
    rawRows = Array.isArray(envelope.data)
      ? envelope.data
      : rowsOf((envelope.data as { rows?: unknown } | null | undefined)?.rows)
  }

  const rows = rawRows.map(normalizeRow).filter((row): row is OcrVolunteerRow => row !== null)
  const warnings = Array.isArray(envelope.warnings)
    ? envelope.warnings.filter(
        (item): item is string => typeof item === 'string' && item.trim() !== ''
      )
    : undefined

  return warnings && warnings.length ? { rows, warnings } : { rows }
}

/** 上传登记表截图 / PDF，返回规整后的识别行。 */
export async function parseVolunteerSheet(file: File): Promise<OcrVolunteerResponse> {
  const form = new FormData()
  form.append('file', file)

  const response = await axios({
    method: 'post',
    url: OCR_ENDPOINT,
    data: form,
    timeout: 60000
  })

  return extractPayload(response?.data)
}

/**
 * 把 OCR 网关返回的纯文本拆成义工登记行。
 *
 * OCR 只给「一行一行」的文字，不保证表格结构；这里用保守规则提取学号、姓名、
 * 班级、时长和模式。拿不准的字段留空，交给页面用本地学生名单与人工复核补全，
 * 绝不为了凑行数猜学号或姓名。
 */

export type OcrVolunteerMode = 'on-campus' | 'off-campus' | 'social-practice'

export interface OcrVolunteerRow {
  studentId?: string
  name?: string
  duration?: number
  mode?: OcrVolunteerMode
  note?: string
}

const FULL_WIDTH_DIGITS = /[\uFF10-\uFF19]/g
const ZERO_WIDTH = /[\u200B-\u200F\u202A-\u202E\uFEFF]/g
const HEADER_TOKENS = [
  '学号',
  '编号',
  '姓名',
  '名字',
  '班级',
  '时长',
  '小时',
  '序号',
  '备注',
  '签名'
]
const DATE_RE = /\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?/g
const PHONE_RE = /(^|\D)1[3-9]\d{9}(?!\d)/g
const CLASS_RE =
  /(?:\d{2,4}级)?[\u4e00-\u9fa5]{0,4}[（(]?\d{1,2}[）)]?班|[\u4e00-\u9fa5]{2,6}班|[\u4e00-\u9fa5]{1,4}[（(]\d{1,2}[）)]/g
const NAME_RE = /[\u4e00-\u9fa5]{2,4}(?:[·•][\u4e00-\u9fa5]{2,8})?/g

const NAME_STOP = new Set([
  '学号',
  '编号',
  '姓名',
  '名字',
  '班级',
  '时长',
  '小时',
  '工时',
  '序号',
  '备注',
  '签名',
  '签字',
  '日期',
  '时间',
  '项目',
  '名称',
  '内容',
  '地点',
  '合计',
  '总计',
  '小计',
  '统计',
  '汇总',
  '名单',
  '登记',
  '志愿',
  '服务',
  '活动',
  '义工',
  '校内',
  '校外',
  '社会',
  '实践',
  '同学',
  '老师',
  '教师',
  '班长',
  '组长',
  '队长',
  '主任',
  '书记',
  '主席',
  '成员',
  '负责',
  '负责人',
  '班主任',
  '学生'
])

const NOTE_STOP = new Set([
  '学号',
  '姓名',
  '班级',
  '时长',
  '序号',
  '备注',
  '签名',
  '签字',
  '日期',
  '时间',
  '合计',
  '总计',
  '小计',
  '统计',
  '汇总',
  '名单',
  '登记',
  '志愿',
  '服务',
  '活动',
  '义工',
  '校内',
  '校外',
  '社会',
  '实践'
])

const NAME_SUFFIX =
  /(?:班|级|校|部|系|院|组|队|课|室|处|会|社|团|中心|同学|老师|教师|班长|组长|队长|主任|书记|主席|成员|负责|签字|签名)$/
const NAME_PREFIX =
  /^(?:高一|高二|高三|初一|初二|初三|大一|大二|大三|大四|年级|班级|校内|校外|社会|实践|志愿|服务|活动|义工|登记|名单|统计|汇总|合计|总计|备注|签名|日期|时间|序号|学号|姓名)/

function toAsciiDigits(value: string): string {
  return value.replace(FULL_WIDTH_DIGITS, (char) =>
    String.fromCharCode(char.charCodeAt(0) - 0xff10 + 0x30)
  )
}

function normalizeLine(value: string): string {
  return toAsciiDigits(value)
    .replace(ZERO_WIDTH, '')
    .replace(/\u3000/g, ' ')
    .replace(/[|｜]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function isHeader(line: string): boolean {
  const compact = line.replace(/\s+/g, '')
  if (!compact || /\d{6,}/.test(compact)) return false
  return HEADER_TOKENS.filter((token) => compact.includes(token)).length >= 2
}

function isNoise(line: string): boolean {
  const compact = line.replace(/\s+/g, '')
  if (!compact) return true
  if (/^(?:第?\d+页|共\d+页|制表|审核|负责人|日期|时间|说明|注意)/.test(compact)) return true
  if (/^(?:义工|志愿|服务|活动).{0,20}(?:登记|名单|统计|汇总|签到|签退)(?:表|单)?$/.test(compact)) {
    return true
  }
  if (/^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?$/.test(compact)) return true
  if (/^(?:合计|总计|小计)/.test(compact)) return true
  return false
}

function stripLabels(line: string): string {
  return line.replace(
    /(?:学号|编号|姓名|名字|班级|时长|小时|工时|模式|类型|备注|序号)\s*[:：]?/g,
    ' '
  )
}

function sliceOut(line: string, start: number, length: number): string {
  return `${line.slice(0, start)} ${line.slice(start + length)}`
}

function extractMode(line: string): { mode: OcrVolunteerMode; start: number; raw: string } | null {
  const patterns: Array<[OcrVolunteerMode, RegExp]> = [
    ['social-practice', /社会实践|校外实践|校内实践|实践/],
    ['on-campus', /校内/],
    ['off-campus', /校外/]
  ]
  for (const [mode, pattern] of patterns) {
    const match = line.match(pattern)
    if (match && match.index !== undefined) {
      return { mode, start: match.index, raw: match[0] }
    }
  }
  return null
}

function stripClass(line: string): string {
  CLASS_RE.lastIndex = 0
  const match = CLASS_RE.exec(line)
  return match && match.index !== undefined ? sliceOut(line, match.index, match[0].length) : line
}

function stripLeadingIndex(line: string): string {
  return line.replace(/^\s*(?:序号\s*)?\d{1,4}\s*[.、)．]?\s+/, ' ')
}

function extractStudentId(line: string): { value: string; raw: string; start: number } | null {
  // 连续学号优先；OCR 偶尔在学号中间插空格，第二分支再尝试 4+3 / 5+4 之类。
  const contiguous = line.match(/(^|[^\d])(\d{7,14})(?!\d)/)
  if (contiguous) {
    return {
      value: contiguous[2],
      raw: contiguous[2],
      start: (contiguous.index ?? 0) + contiguous[1].length
    }
  }
  const spaced = line.match(/(^|[^\d])(\d{3,6})[ \t]+(\d{3,8})(?!\d)/)
  if (spaced) {
    const value = `${spaced[2]}${spaced[3]}`
    if (value.length >= 7 && value.length <= 14) {
      return {
        value,
        raw: spaced[0].slice(spaced[1].length),
        start: (spaced.index ?? 0) + spaced[1].length
      }
    }
  }
  return null
}

function extractDuration(
  line: string,
  hasStudentId: boolean
): { value: number; start: number; raw: string } | null {
  const suffix = line.match(/(\d+(?:\.\d+)?)\s*(?:h|H|小时|时|课时)/)
  if (suffix && suffix.index !== undefined) {
    const value = Number(suffix[1])
    if (Number.isFinite(value) && value > 0 && value <= 24) {
      return { value, start: suffix.index, raw: suffix[0] }
    }
  }

  const numbers: Array<{ value: number; start: number; raw: string }> = []
  const re = /(^|[^\d.])(\d+(?:\.\d+)?)(?![\d.])/g
  let match: RegExpExecArray | null
  while ((match = re.exec(line))) {
    const value = Number(match[2])
    if (!Number.isFinite(value) || value <= 0 || value > 24) continue
    numbers.push({
      value,
      start: (match.index ?? 0) + match[1].length,
      raw: match[2]
    })
  }
  if (!numbers.length) return null

  // 一列行号 + 一列时长时取最后一个；没有学号且数字在行首、后面紧跟中文时更像序号。
  const candidate = numbers[numbers.length - 1]
  if (!hasStudentId && candidate.start === 0) {
    const tail = line.slice(candidate.raw.length)
    if (/[\u4e00-\u9fa5]/.test(tail)) return null
  }
  return candidate
}

function isNameCandidate(value: string): boolean {
  if (NAME_STOP.has(value)) return false
  if (NAME_PREFIX.test(value)) return false
  if (NAME_SUFFIX.test(value)) return false
  return true
}

function extractName(line: string): { value: string; start: number; raw: string } | null {
  NAME_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = NAME_RE.exec(line))) {
    if (isNameCandidate(match[0])) {
      return { value: match[0], start: match.index ?? 0, raw: match[0] }
    }
  }
  return null
}

function cleanupNote(line: string): string {
  let note = line
    .replace(/[|｜,，;；:：/\\]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  note = note.replace(/^[-—–·.、]+|[-—–·.、]+$/g, '').trim()
  if (!note) return ''
  if (NOTE_STOP.has(note)) return ''
  if (/^(?:元|分|人|次|页|无)$/.test(note)) return ''
  return note.slice(0, 80)
}

function parseLine(line: string): OcrVolunteerRow | null {
  if (isHeader(line) || isNoise(line)) return null

  let work = stripLabels(line)
  const mode = extractMode(work)
  if (mode) work = sliceOut(work, mode.start, mode.raw.length)

  work = work.replace(DATE_RE, ' ').replace(PHONE_RE, '$1 ')
  work = stripClass(work)

  // 先认学号再削行号：`2024 001` 这种被 OCR 插空格的学号，不能被当成「行号 2024」。
  const id = extractStudentId(work)
  if (id) {
    work = work.replace(id.raw, ' ')
    if (id.start > 0) work = stripLeadingIndex(work)
  } else {
    work = stripLeadingIndex(work)
  }

  const duration = extractDuration(work, Boolean(id))
  if (duration) work = sliceOut(work, duration.start, duration.raw.length)

  const name = extractName(work)
  if (name) work = sliceOut(work, name.start, name.raw.length)

  const note = cleanupNote(work)
  if (!id && !name) return null

  return {
    studentId: id?.value,
    name: name?.value,
    duration: duration?.value,
    mode: mode?.mode,
    note: note || undefined
  }
}

export function parseOcrText(text: string): OcrVolunteerRow[] {
  if (typeof text !== 'string' || !text.trim()) return []

  const lines = text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => normalizeLine(line))
    .filter(Boolean)

  const rows: OcrVolunteerRow[] = []
  const seen = new Set<string>()
  for (const line of lines) {
    const row = parseLine(line)
    if (!row) continue
    const key = row.studentId ? `id:${row.studentId}` : `line:${line}`
    if (seen.has(key)) continue
    seen.add(key)
    rows.push(row)
  }
  return rows
}

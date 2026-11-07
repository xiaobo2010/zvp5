import axios from '@/plugins/axios.ts'

/**
 * 本地学生名单（学号 ↔ 姓名）。
 *
 * 目的：Excel 式志愿登记页需要在几千个单元格里做「学号找姓名 / 姓名找学号」，
 * 每个单元格都发一次请求既慢又会被限流。这里把整份名单一次性落到 IndexedDB
 * （库名 `index.db`），只在用户显式刷新时整批同步一次。
 *
 * 约定：
 * - 读写都带 1500ms 兜底超时，IndexedDB 不可用（隐私模式、老浏览器）时函数
 *   返回空结果而不是挂起页面。
 * - `loadStudentIndex()` 只读缓存，`syncStudentIndex()` 才走网络；失败时回退到
 *   旧缓存并把原因放在 `error` 里，普通网络故障不抛异常。
 */

export interface StudentRecord {
  oid: string
  studentId: string
  name: string
  classId: string
  className: string
}

export type StudentIndexSource = 'index.db' | 'network' | 'empty'

export interface StudentIndexState {
  students: StudentRecord[]
  updatedAt: number
  source: StudentIndexSource
  error?: string
}

export type StudentMatchStatus = 'ok' | 'ambiguous' | 'mismatch' | 'missing' | 'empty'

export interface StudentMatch {
  status: StudentMatchStatus
  record: StudentRecord | null
  candidates: StudentRecord[]
  message: string
}

/** 缓存有效期：超过这个时间只影响提示，不影响离线可用性。 */
export const STUDENT_INDEX_TTL_MS = 24 * 60 * 60 * 1000

const DB_NAME = 'index.db'
const DB_VERSION = 1
const STUDENTS_STORE = 'students'
const META_STORE = 'meta'
const META_KEY = 'students'
const IDB_TIMEOUT_MS = 1500

const USER_PAGE_SIZE = 1000
const GROUP_PAGE_SIZE = 100
const MAX_USER_PAGES = 30
const MAX_GROUP_PAGES = 60
const MAX_STUDENTS = 20000
const MAX_CANDIDATES = 8

interface StudentRow extends StudentRecord {
  /** 归一化后的姓名键，作为 `students` 的索引。 */
  nameKey: string
}

interface MetaRow {
  key: string
  updatedAt: number
  total: number
}

interface RawUserRow {
  _id?: unknown
  id?: unknown
  name?: unknown
  group?: unknown
  class_id?: unknown
}

interface RawGroupRow {
  _id?: unknown
  name?: unknown
  type?: unknown
}

interface PagedResponse<T> {
  data?: T[] | null
  total?: unknown
}

// ---------------------------------------------------------------------------
// 归一化
// ---------------------------------------------------------------------------

const FULL_WIDTH_DIGITS = /[\uFF10-\uFF19]/g

/** 全角数字 → ASCII。 */
function toAsciiDigits(input: string): string {
  return input.replace(FULL_WIDTH_DIGITS, (char) =>
    String.fromCharCode(char.charCodeAt(0) - 0xff10 + 0x30)
  )
}

/**
 * 学号归一化：全角转半角、去首尾空白与内部空白。
 *
 * 纯数字结果直接返回；带字母/连字符等异常内容也原样返回（只是清理空白），
 * 交给匹配逻辑判定，避免把「2024-001」这类脏数据静默改成别的学号。
 */
export function normalizeStudentId(raw: string): string {
  if (typeof raw !== 'string') return ''
  return toAsciiDigits(raw).replace(/\s+/g, '')
}

const ZERO_WIDTH = /[\u200B-\u200F\u202A-\u202E\uFEFF]/g
const WHITESPACE = /[\s\u3000]+/g

/** 姓名归一化：去零宽字符、空白折叠为单个半角空格、去首尾空白。 */
export function normalizePersonName(raw: string): string {
  if (typeof raw !== 'string') return ''
  return raw.replace(ZERO_WIDTH, '').replace(WHITESPACE, ' ').trim()
}

/** 姓名比较键：不区分大小写，且忽略姓名内部空格，避免「张 三」对不上「张三」。 */
function nameKeyOf(raw: string): string {
  return normalizePersonName(raw).toLowerCase().replace(/\s+/g, '')
}

// 同一批记录会被成千上万个单元格反复查询，姓名键只算一次。
const nameKeyCache = new WeakMap<StudentRecord, string>()

function cachedNameKey(record: StudentRecord): string {
  let key = nameKeyCache.get(record)
  if (key === undefined) {
    key = nameKeyOf(record.name)
    nameKeyCache.set(record, key)
  }
  return key
}

// ---------------------------------------------------------------------------
// 纯查询（不碰网络 / IndexedDB）
// ---------------------------------------------------------------------------

function emptyLookup(): { exact: StudentRecord | null; candidates: StudentRecord[] } {
  return { exact: null, candidates: [] }
}

function collectCandidates(
  lists: StudentRecord[][],
  limit: number = MAX_CANDIDATES
): StudentRecord[] {
  const out: StudentRecord[] = []
  const seen = new Set<StudentRecord>()
  for (const list of lists) {
    for (const record of list) {
      if (out.length >= limit) return out
      if (seen.has(record)) continue
      seen.add(record)
      out.push(record)
    }
  }
  return out
}

/**
 * 精确查找 + 候选。
 *
 * - `id`：先精确学号；候选按「前缀优先、包含其次」补足，最多 8 条。
 * - `name`：精确同名可能有多个。多于一个时 `exact` 为 null，全部同名者放进
 *   `candidates`，由 UI 让用户自己选（绝不替用户挑一个同名同学）。
 */
export function lookupStudent(
  students: StudentRecord[],
  query: string,
  field: 'id' | 'name'
): { exact: StudentRecord | null; candidates: StudentRecord[] } {
  const idKey = field === 'id' ? normalizeStudentId(query) : ''
  const personKey = field === 'name' ? nameKeyOf(query) : ''
  if (!idKey && !personKey) return emptyLookup()

  const exacts: StudentRecord[] = []
  const prefixes: StudentRecord[] = []
  const contains: StudentRecord[] = []

  for (const record of students) {
    if (field === 'id') {
      if (record.studentId === idKey) {
        exacts.push(record)
        continue
      }
      if (record.studentId.startsWith(idKey)) prefixes.push(record)
      else if (record.studentId.includes(idKey)) contains.push(record)
    } else {
      const key = cachedNameKey(record)
      if (key === personKey) {
        exacts.push(record)
        continue
      }
      if (key.startsWith(personKey)) prefixes.push(record)
      else if (key.includes(personKey)) contains.push(record)
    }
  }

  const exact = exacts.length === 1 ? exacts[0] : null
  // 同名多条时把它们整体交给用户，再补前缀/包含候选。
  const candidates = collectCandidates([exacts.length > 1 ? exacts : [], prefixes, contains])
  return { exact, candidates }
}

/** 输入提示：前缀优先、包含其次，去重后最多 `limit` 条。 */
export function suggestStudents(
  students: StudentRecord[],
  query: string,
  field: 'id' | 'name',
  limit: number = MAX_CANDIDATES
): StudentRecord[] {
  const max = Number.isFinite(limit) ? Math.max(0, Math.trunc(limit)) : MAX_CANDIDATES
  if (max === 0) return []

  const idKey = field === 'id' ? normalizeStudentId(query) : ''
  const personKey = field === 'name' ? nameKeyOf(query) : ''
  if (!idKey && !personKey) return []

  const prefixes: StudentRecord[] = []
  const contains: StudentRecord[] = []

  for (const record of students) {
    const key = field === 'id' ? record.studentId : cachedNameKey(record)
    const needle = field === 'id' ? idKey : personKey
    if (key.startsWith(needle)) prefixes.push(record)
    else if (key.includes(needle)) contains.push(record)
  }

  return collectCandidates([prefixes, contains], max)
}

/**
 * 一行（学号 + 姓名）与本地名单的对照结果。
 *
 * 只有「学号命中且姓名一致」或「只有姓名且唯一命中」才算 `ok`；
 * 学号姓名互相矛盾返回 `mismatch`（`record` 给出可信的一方），同名多人返回
 * `ambiguous` 且 `record` 为 null。
 */
export function matchStudentRow(
  students: StudentRecord[],
  rawId: string,
  rawName: string
): StudentMatch {
  const idKey = normalizeStudentId(rawId)
  const personKey = nameKeyOf(rawName)

  if (!idKey && !personKey) {
    return { status: 'empty', record: null, candidates: [], message: '学号与姓名均为空' }
  }

  if (idKey && !personKey) {
    const record = students.find((student) => student.studentId === idKey) ?? null
    if (record) {
      return { status: 'ok', record, candidates: [record], message: '已由学号匹配' }
    }
    return { status: 'missing', record: null, candidates: [], message: '本地名单未找到该学号' }
  }

  if (!idKey && personKey) {
    const sameName = students.filter((student) => cachedNameKey(student) === personKey)
    if (sameName.length === 1) {
      return {
        status: 'ok',
        record: sameName[0],
        candidates: [sameName[0]],
        message: '已由姓名匹配'
      }
    }
    if (sameName.length > 1) {
      return {
        status: 'ambiguous',
        record: null,
        candidates: collectCandidates([sameName]),
        message: '存在同名同学，请选择'
      }
    }
    return { status: 'missing', record: null, candidates: [], message: '本地名单未找到该姓名' }
  }

  const idRecord = students.find((student) => student.studentId === idKey) ?? null
  const sameName = students.filter((student) => cachedNameKey(student) === personKey)

  if (idRecord) {
    if (cachedNameKey(idRecord) === personKey) {
      return { status: 'ok', record: idRecord, candidates: [idRecord], message: '已由学号匹配' }
    }
    // 学号可信、姓名对不上：把学号对应的人作为 record，同时列出姓名候选。
    return {
      status: 'mismatch',
      record: idRecord,
      candidates: collectCandidates([[idRecord], sameName]),
      message: '学号与姓名不一致'
    }
  }

  if (sameName.length === 1) {
    return {
      status: 'mismatch',
      record: sameName[0],
      candidates: [sameName[0]],
      message: '学号与姓名不一致'
    }
  }
  if (sameName.length > 1) {
    return {
      status: 'ambiguous',
      record: null,
      candidates: collectCandidates([sameName]),
      message: '存在同名同学，请选择'
    }
  }

  return {
    status: 'missing',
    record: null,
    candidates: [],
    message: '本地名单未找到该学号与姓名'
  }
}

// ---------------------------------------------------------------------------
// IndexedDB
// ---------------------------------------------------------------------------

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDB(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null)
  if (dbPromise) return dbPromise

  dbPromise = new Promise((resolve) => {
    let settled = false
    const finish = (db: IDBDatabase | null) => {
      if (settled) {
        db?.close()
        return
      }
      settled = true
      clearTimeout(timer)
      resolve(db)
    }
    // IndexedDB 在隐私模式下可能永远不给回调，兜底超时后当作不可用。
    const timer = setTimeout(() => finish(null), IDB_TIMEOUT_MS)

    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION)
      request.onupgradeneeded = () => {
        const db = request.result
        if (!db.objectStoreNames.contains(STUDENTS_STORE)) {
          const store = db.createObjectStore(STUDENTS_STORE, { keyPath: 'studentId' })
          store.createIndex('nameKey', 'nameKey', { unique: false })
        }
        if (!db.objectStoreNames.contains(META_STORE)) {
          db.createObjectStore(META_STORE, { keyPath: 'key' })
        }
      }
      request.onsuccess = () => {
        const db = request.result
        // 其他标签页升级/删除库时主动让路，避免 blocked。
        db.onversionchange = () => {
          db.close()
          dbPromise = null
        }
        finish(db)
      }
      request.onerror = request.onblocked = () => finish(null)
    } catch {
      finish(null)
    }
  })

  return dbPromise
}

async function withStores<T>(
  mode: IDBTransactionMode,
  stores: string[],
  fallback: T,
  operation: (tx: IDBTransaction, setResult: (value: T) => void) => void
): Promise<T> {
  const db = await openDB()
  if (!db) return fallback

  return new Promise<T>((resolve) => {
    let result = fallback
    let settled = false
    const finish = (value: T) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(value)
    }
    // Large rosters are written in one transaction; 1.5s is enough for reads
    // but can cut off a 10k+ row write on a slow tablet. Keep reads responsive
    // while giving the bulk write a wider window.
    const timer = setTimeout(() => finish(fallback), mode === 'readwrite' ? 8000 : IDB_TIMEOUT_MS)

    try {
      const tx = db.transaction(stores, mode)
      tx.oncomplete = () => finish(result)
      tx.onerror = tx.onabort = () => finish(fallback)
      operation(tx, (value) => {
        result = value
      })
    } catch {
      finish(fallback)
    }
  })
}

function toRecord(row: unknown): StudentRecord | null {
  if (!row || typeof row !== 'object') return null
  const raw = row as Partial<StudentRecord>
  const studentId = normalizeStudentId(String(raw.studentId ?? ''))
  const name = normalizePersonName(String(raw.name ?? ''))
  if (!studentId || !name) return null
  return {
    oid: String(raw.oid ?? ''),
    studentId,
    name,
    classId: String(raw.classId ?? ''),
    className: String(raw.className ?? '')
  }
}

async function readStudents(): Promise<StudentRecord[]> {
  return withStores<StudentRecord[]>('readonly', [STUDENTS_STORE], [], (tx, setResult) => {
    const request = tx.objectStore(STUDENTS_STORE).getAll()
    request.onsuccess = () => {
      const rows = Array.isArray(request.result) ? request.result : []
      const records: StudentRecord[] = []
      for (const row of rows) {
        const record = toRecord(row)
        if (record) records.push(record)
      }
      setResult(records)
    }
  })
}

async function readUpdatedAt(): Promise<number> {
  return withStores<number>('readonly', [META_STORE], 0, (tx, setResult) => {
    const request = tx.objectStore(META_STORE).get(META_KEY)
    request.onsuccess = () => {
      const row = request.result as MetaRow | undefined
      const updatedAt = Number(row?.updatedAt)
      setResult(Number.isFinite(updatedAt) && updatedAt > 0 ? updatedAt : 0)
    }
  })
}

async function writeStudents(students: StudentRecord[], updatedAt: number): Promise<void> {
  await withStores<undefined>('readwrite', [STUDENTS_STORE, META_STORE], undefined, (tx) => {
    const store = tx.objectStore(STUDENTS_STORE)
    store.clear()
    for (const student of students) {
      const row: StudentRow = { ...student, nameKey: cachedNameKey(student) }
      store.put(row)
    }
    const meta: MetaRow = { key: META_KEY, updatedAt, total: students.length }
    tx.objectStore(META_STORE).put(meta)
  })
}

// ---------------------------------------------------------------------------
// 网络同步
// ---------------------------------------------------------------------------

let inFlightSync: Promise<StudentIndexState> | null = null

function toFiniteNumber(value: unknown): number {
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) && num > 0 ? num : 0
}

function pageRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (!payload || typeof payload !== 'object') return []
  const data = (payload as PagedResponse<T>).data
  return Array.isArray(data) ? data : []
}

function pageTotal(payload: unknown): number {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return 0
  return toFiniteNumber((payload as PagedResponse<unknown>).total)
}

function describeError(error: unknown): string {
  if (error && typeof error === 'object' && 'isAxiosError' in error) {
    const status = (error as { response?: { status?: number } }).response?.status
    return typeof status === 'number'
      ? `名单同步失败（HTTP ${status}）`
      : '名单同步失败（网络不可用）'
  }
  if (error instanceof Error && error.message) return `名单同步失败：${error.message}`
  return '名单同步失败'
}

/**
 * 班级 id → 班级名。班级名只用于展示，拿不到就留空，不影响学号/姓名的匹配，
 * 因此这里吞掉异常（普通学生角色无权读 /v1/groups）。
 */
async function fetchClassNames(): Promise<Map<string, string>> {
  const names = new Map<string, string>()
  try {
    for (let page = 1; page <= MAX_GROUP_PAGES; page += 1) {
      const response = await axios({
        method: 'get',
        url: '/v1/groups',
        params: { page, size: GROUP_PAGE_SIZE }
      })
      const payload = response?.data
      const rows = pageRows<RawGroupRow>(payload)
      const total = pageTotal(payload)

      for (const row of rows) {
        if (!row || typeof row !== 'object') continue
        const id = String(row._id ?? '')
        const name = normalizePersonName(String(row.name ?? ''))
        // 班级行没有 type 字段，权限组显式标了 'permission'。
        const type = typeof row.type === 'string' ? row.type : ''
        if (!id || !name || type === 'permission') continue
        names.set(id, name)
      }

      if (!rows.length) break
      if (total > 0 && page * GROUP_PAGE_SIZE >= total) break
    }
  } catch {
    // 退化为「班级名为空」。
  }
  return names
}

function mapUser(row: RawUserRow, classNames: Map<string, string>): StudentRecord | null {
  if (!row || typeof row !== 'object') return null
  const studentId = normalizeStudentId(String(row.id ?? ''))
  const name = normalizePersonName(String(row.name ?? ''))
  if (!studentId || !name) return null

  const firstGroup = Array.isArray(row.group) ? row.group[0] : undefined
  const classId = String(firstGroup ?? row.class_id ?? '')
  return {
    oid: String(row._id ?? ''),
    studentId,
    name,
    classId,
    className: classNames.get(classId) ?? ''
  }
}

async function fetchStudents(
  classNames: Map<string, string>,
  onProgress?: (loaded: number, total: number) => void
): Promise<StudentRecord[]> {
  // 按归一化学号去重：同一学号只保留第一次出现的那条。
  const collected = new Map<string, StudentRecord>()
  let fetched = 0
  let total = 0

  for (let page = 1; page <= MAX_USER_PAGES; page += 1) {
    const response = await axios({
      method: 'get',
      url: '/v1/users',
      params: { page, size: USER_PAGE_SIZE, query: '' },
      timeout: 30000
    })
    const payload = response?.data
    const rows = pageRows<RawUserRow>(payload)
    const reported = pageTotal(payload)
    if (reported > total) total = reported

    for (const row of rows) {
      if (collected.size >= MAX_STUDENTS) break
      const record = mapUser(row, classNames)
      if (!record) continue
      if (!collected.has(record.studentId)) collected.set(record.studentId, record)
    }

    fetched += rows.length
    onProgress?.(fetched, Math.max(total, fetched))

    if (!rows.length) break
    if (total > 0 && fetched >= total) break
    if (fetched >= MAX_STUDENTS || collected.size >= MAX_STUDENTS) break
  }

  return [...collected.values()]
}

async function runSync(
  onProgress?: (loaded: number, total: number) => void
): Promise<StudentIndexState> {
  try {
    const classNames = await fetchClassNames()
    const students = await fetchStudents(classNames, onProgress)
    const updatedAt = Date.now()

    // 空名单不覆盖旧缓存：更可能是接口异常而不是学校真的没有学生。
    if (students.length) await writeStudents(students, updatedAt)

    return { students, updatedAt, source: 'network' }
  } catch (error) {
    const message = describeError(error)
    const stale = await loadStudentIndex()
    if (stale.students.length) {
      return { ...stale, source: 'index.db', error: message }
    }
    return { students: [], updatedAt: 0, source: 'empty', error: message }
  }
}

// ---------------------------------------------------------------------------
// 对外接口
// ---------------------------------------------------------------------------

/** 只读本地缓存；不做任何网络请求，页面自己决定何时刷新。 */
export async function loadStudentIndex(): Promise<StudentIndexState> {
  const [students, updatedAt] = await Promise.all([readStudents(), readUpdatedAt()])
  if (!students.length) {
    return { students: [], updatedAt: 0, source: 'empty' }
  }
  return { students, updatedAt, source: 'index.db' }
}

/** 整批拉取名单并落盘；并发调用共享同一个请求。 */
export function syncStudentIndex(
  onProgress?: (loaded: number, total: number) => void
): Promise<StudentIndexState> {
  if (inFlightSync) return inFlightSync
  const guarded = runSync(onProgress).finally(() => {
    inFlightSync = null
  })
  inFlightSync = guarded
  return guarded
}

/** 清空本地名单（登出 / 手动重置用）。 */
export async function clearStudentIndex(): Promise<void> {
  await withStores<undefined>('readwrite', [STUDENTS_STORE, META_STORE], undefined, (tx) => {
    tx.objectStore(STUDENTS_STORE).clear()
    tx.objectStore(META_STORE).delete(META_KEY)
  })
}

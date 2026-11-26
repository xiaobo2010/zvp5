import type { SettingValue, SystemSettingItem } from '@/api/system/settings'

/**
 * 在线设置中心的纯逻辑：控件分支、草稿转换、差异计算、角色额度表的增删行。
 *
 * 这一层不碰 Vue 与网络，页面只负责把结果画出来；因此可以单测。
 */

/** 角色额度表的一行：一个角色 + 各能力的月额度。 */
export interface RoleQuotaRow {
  role: string
  limits: Record<string, number>
}

/**
 * 扁平 dict 的一行：一个能力 + 次数。
 *
 * 与角色额度表是两种东西：扁平 dict 的值形如 { ocr: 10, chat: 20 }，
 * 嵌套 dict 的值形如 { admin: { ocr: 500 } }。前端按值形状分派，不按 key 猜。
 */
export interface FlatDictRow {
  key: string
  value: number
}

/** 草稿值：界面上正在编辑的值。dict 用行数组代替对象，其余与存储值同形。 */
export type SettingDraftValue = string | number | boolean | string[] | RoleQuotaRow[] | FlatDictRow[]

/** 值类型 → 控件类型。页面按这个分支选控件。 */
export type SettingControlKind =
  | 'number'
  | 'switch'
  | 'text'
  | 'multiselect'
  | 'roleQuota'
  | 'flatDict'

/** 一处改动：键、旧值、新值。 */
export interface SettingChange {
  key: string
  label: string
  from: SettingValue
  to: SettingValue
}

/** 一个分区（一组设置项）。 */
export interface SettingSection {
  group: string
  items: SystemSettingItem[]
}

/** 角色额度表的列（能力）顺序：ocr / chat 在前，其余按出现顺序。 */
const ROLE_QUOTA_KIND_ORDER = ['ocr', 'chat']

/** 角色额度表里没有候选值时提供的兜底角色（后端给了 options 就以后端为准）。 */
export const DEFAULT_ROLE_IDS = ['admin', 'department', 'student', 'secretary']

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : 0
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function toBoolean(value: unknown): boolean {
  if (typeof value === 'boolean') {
    return value
  }
  if (typeof value === 'number') {
    return value !== 0
  }
  if (typeof value === 'string') {
    return value === 'true' || value === '1'
  }
  return false
}

function toText(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (value === null || value === undefined) {
    return ''
  }
  return String(value)
}

function toStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((entry) => toText(entry)).filter((entry) => entry !== '')
}

/** 值类型 → 控件类型。未知的类型按文本处理，不让页面崩。 */
export function resolveControlKind(item: SystemSettingItem): SettingControlKind {
  switch (item.type) {
    case 'int':
      return 'number'
    case 'bool':
      return 'switch'
    case 'str':
    case 'text':
      return 'text'
    case 'list':
      return 'multiselect'
    case 'dict':
      // dict 有两种形状：值本身是对象 → 角色×能力表；值是数字 → 键值行。
      return dictShape(item.value) === 'flat' ? 'flatDict' : 'roleQuota'
    default:
      return 'text'
  }
}

/**
 * dict 的形状判定：只看值长什么样，不看 key。
 *
 * - flat：{ ocr: 10, chat: 20 }，每个值都是标量；
 * - nested：{ admin: { ocr: 500 } }，值是对象。
 *
 * 空对象与非对象一律算 nested，交给角色额度表去显示空态（保留「添加角色」入口），
 * 扁平 dict 在没有键可画时只会是一张空表，没有意义。
 */
export function dictShape(value: unknown): 'flat' | 'nested' {
  if (!isPlainObject(value)) {
    return 'nested'
  }
  const entries = Object.entries(value)
  if (!entries.length) {
    return 'nested'
  }
  return entries.every(([, entry]) => !isPlainObject(entry) && !Array.isArray(entry))
    ? 'flat'
    : 'nested'
}

/**
 * 扁平 dict 要渲染的键。
 *
 * 键清单只能来自后端：有 options 就按 options 的顺序排（后端认哪些能力由它说了算），
 * 没有 options 就用现有值的键序。两种情况都**不新增**未知键 —— 后端只认自己那几个
 * 能力，凭空补一个 0 会被打回，或给永不查询的键设上限。
 */
export function flatDictKeys(item: SystemSettingItem): string[] {
  return orderedFlatKeys(item.value, item.options)
}

function orderedFlatKeys(value: unknown, options: string[] | null): string[] {
  const existing = isPlainObject(value) ? Object.keys(value) : []
  const candidates = (options ?? []).filter(
    (option) => typeof option === 'string' && option.trim() !== ''
  )
  if (!candidates.length) {
    return existing
  }
  const preferred = candidates.filter((option) => existing.includes(option))
  return [...preferred, ...existing.filter((key) => !preferred.includes(key))]
}

/** 存储值 → 扁平 dict 的行。键名只读，值不是数字时给 0。 */
export function flatDictRows(
  item: SystemSettingItem,
  value: SettingValue | null = item.value
): FlatDictRow[] {
  if (!isPlainObject(value)) {
    return []
  }
  const source = value as Record<string, unknown>
  return orderedFlatKeys(value, item.options).map((key) => ({
    key,
    value: toNumber(source[key])
  }))
}

/** 扁平 dict 的行 → 存储值（后端要求整数，这里向下取整）。 */
export function flatDictValue(rows: FlatDictRow[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const row of rows) {
    const key = row.key.trim()
    if (!key) {
      continue
    }
    out[key] = Number.isFinite(row.value) ? Math.trunc(row.value) : 0
  }
  return out
}

/** 扁平 dict 的数字下界：后端给就用，没给按 0（负的上限没有意义）。 */
export function flatDictMin(item: SystemSettingItem): number {
  return typeof item.min === 'number' ? item.min : 0
}

/** list 类型是否允许自由输入（没有候选值时允许管理员自填）。 */
export function allowsFreeEntry(item: SystemSettingItem): boolean {
  return resolveControlKind(item) === 'multiselect' && !(item.options?.length ?? 0)
}

/** 角色额度表的候选角色。 */
export function roleOptions(item: SystemSettingItem): string[] {
  const options = (item.options ?? []).filter((option) => typeof option === 'string' && option.trim())
  return options.length ? options : [...DEFAULT_ROLE_IDS]
}

/** 存储值 → 角色额度表的行。 */
export function roleQuotaRows(value: unknown): RoleQuotaRow[] {
  if (!isPlainObject(value)) {
    return []
  }
  return Object.entries(value).map(([role, limits]) => {
    const row: Record<string, number> = {}
    if (isPlainObject(limits)) {
      for (const [kind, limit] of Object.entries(limits)) {
        row[kind] = toNumber(limit)
      }
    }
    return { role, limits: row }
  })
}

/** 角色额度表的列（能力名）。ocr / chat 排前面，其余按出现顺序。 */
export function roleQuotaColumns(rows: RoleQuotaRow[]): string[] {
  const seen: string[] = []
  for (const row of rows) {
    for (const kind of Object.keys(row.limits)) {
      if (!seen.includes(kind)) {
        seen.push(kind)
      }
    }
  }
  const preferred = ROLE_QUOTA_KIND_ORDER.filter((kind) => seen.includes(kind))
  const rest = seen.filter((kind) => !ROLE_QUOTA_KIND_ORDER.includes(kind))
  return [...preferred, ...rest]
}

/** 角色额度表的行 → 存储值（角色名为空的行直接丢掉）。 */
export function roleQuotaDict(rows: RoleQuotaRow[]): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {}
  for (const row of rows) {
    const role = row.role.trim()
    if (!role) {
      continue
    }
    out[role] = { ...row.limits }
  }
  return out
}

/** 追加一行（角色名默认空，额度按当前列铺 0）。 */
export function addRoleRow(
  rows: RoleQuotaRow[],
  columns: string[] = [],
  role = ''
): RoleQuotaRow[] {
  const limits: Record<string, number> = {}
  for (const column of columns) {
    limits[column] = 0
  }
  return [...rows, { role, limits }]
}

/** 删除一行（按行序号）。序号越界时原样返回。 */
export function removeRoleRow(rows: RoleQuotaRow[], index: number): RoleQuotaRow[] {
  if (index < 0 || index >= rows.length) {
    return rows
  }
  return rows.filter((_row, position) => position !== index)
}

/** 存储值 → 草稿值。 */
export function draftFromValue(item: SystemSettingItem, value: SettingValue): SettingDraftValue {
  switch (resolveControlKind(item)) {
    case 'number':
      return toNumber(value)
    case 'switch':
      return toBoolean(value)
    case 'text':
      return toText(value)
    case 'multiselect':
      return toStringList(value)
    case 'roleQuota':
      return roleQuotaRows(value)
    case 'flatDict':
      return flatDictRows(item, value)
    default:
      return toText(value)
  }
}

/** 草稿值 → 存储值（提交给后端的形态）。 */
export function storedFromDraft(item: SystemSettingItem, draft: SettingDraftValue): SettingValue {
  switch (resolveControlKind(item)) {
    case 'number':
      return toNumber(draft)
    case 'switch':
      return toBoolean(draft)
    case 'text':
      return toText(draft)
    case 'multiselect':
      return toStringList(draft)
    case 'roleQuota':
      return roleQuotaDict(Array.isArray(draft) ? (draft as RoleQuotaRow[]) : [])
    case 'flatDict':
      // 契约里的 SettingValue 只声明了角色额度那一种 dict 形状，扁平 dict 由后端
      // 真实返回（{ ocr: 10, chat: 20 }），类型上只能显式跨越一次。
      return flatDictValue(Array.isArray(draft) ? (draft as FlatDictRow[]) : []) as unknown as SettingValue
    default:
      return toText(draft)
  }
}

/** 稳定的 JSON 序列化：对象键按字典序，数组保持顺序。 */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value ?? null) ?? 'null'
  }
  if (Array.isArray(value)) {
    return `[${value.map((entry) => stableStringify(entry)).join(',')}]`
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, entry]) => entry !== undefined)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
  return `{${entries.map(([key, entry]) => `${JSON.stringify(key)}:${stableStringify(entry)}`).join(',')}}`
}

/** 深比较两个设置值。 */
export function isSameValue(left: unknown, right: unknown): boolean {
  return stableStringify(left) === stableStringify(right)
}

/**
 * 按控件语义比较：多选是集合，顺序不算变化；其余按值深比较。
 */
export function isSameForKind(
  item: SystemSettingItem,
  left: SettingValue,
  right: SettingValue
): boolean {
  if (resolveControlKind(item) === 'multiselect') {
    return isSameValue(
      [...toStringList(left)].sort(),
      [...toStringList(right)].sort()
    )
  }
  return isSameValue(left, right)
}

/** 计算与存储值的差异（只含真正改动的键）。 */
export function computeChanges(
  items: SystemSettingItem[],
  drafts: Record<string, SettingDraftValue>
): SettingChange[] {
  const changes: SettingChange[] = []
  for (const item of items) {
    const draft = drafts[item.key]
    if (draft === undefined) {
      continue
    }
    const to = storedFromDraft(item, draft)
    if (isSameForKind(item, item.value, to)) {
      continue
    }
    changes.push({ key: item.key, label: item.label, from: item.value, to })
  }
  return changes
}

/** 有未保存改动的键。 */
export function changedKeys(
  items: SystemSettingItem[],
  drafts: Record<string, SettingDraftValue>
): string[] {
  return computeChanges(items, drafts).map((change) => change.key)
}

/** 当前值是否已经偏离内置默认值（默认值为空时不算偏离）。 */
export function differsFromDefault(
  item: SystemSettingItem,
  defaults: Record<string, SettingValue>
): boolean {
  if (!(item.key in defaults)) {
    return false
  }
  return !isSameForKind(item, defaults[item.key], item.value)
}

/** 把设置项按分组归拢：ai / ops 固定在前，其余分组按出现顺序补在后面。 */
export function groupSections(items: SystemSettingItem[]): SettingSection[] {
  const groups = new Map<string, SystemSettingItem[]>()
  for (const item of items) {
    const bucket = groups.get(item.group)
    if (bucket) {
      bucket.push(item)
    } else {
      groups.set(item.group, [item])
    }
  }
  const preferred = ['ai', 'ops'].filter((group) => groups.has(group))
  const rest = [...groups.keys()].filter((group) => !preferred.includes(group))
  return [...preferred, ...rest].map((group) => ({ group, items: groups.get(group) ?? [] }))
}

/** 展示用的取值文案。布尔与空值的说法由调用方通过 words 提供。 */
export interface FormatWords {
  on: string
  off: string
  empty: string
}

export function formatSettingValue(value: SettingValue, words: FormatWords): string {
  if (value === null || value === undefined) {
    return words.empty
  }
  if (typeof value === 'boolean') {
    return value ? words.on : words.off
  }
  if (typeof value === 'number') {
    return String(value)
  }
  if (typeof value === 'string') {
    return value === '' ? words.empty : value
  }
  if (Array.isArray(value)) {
    if (!value.length) {
      return words.empty
    }
    return value.map((entry) => formatSettingValue(entry, words)).join('、')
  }
  // dict：两种形状，扁平是「键 值」，嵌套是「角色：能力 额度」
  const entries = Object.entries(value)
  if (!entries.length) {
    return words.empty
  }
  if (entries.every(([, entry]) => !isPlainObject(entry))) {
    return entries
      .map(
        ([key, entry]) =>
          `${key} ${formatSettingValue(entry as unknown as SettingValue, words)}`
      )
      .join('，')
  }
  return entries
    .map(([role, limits]) => {
      const kinds = Object.entries(limits ?? {})
      if (!kinds.length) {
        return `${role}：${words.empty}`
      }
      return `${role}：${kinds.map(([kind, limit]) => `${kind} ${limit}`).join('，')}`
    })
    .join('，')
}

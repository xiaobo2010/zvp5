import { describe, expect, it } from 'vitest'
import type { SystemSettingItem } from '@/api/system/settings'
import {
  addRoleRow,
  allowsFreeEntry,
  changedKeys,
  computeChanges,
  dictShape,
  differsFromDefault,
  draftFromValue,
  flatDictKeys,
  flatDictMin,
  flatDictRows,
  flatDictValue,
  formatSettingValue,
  groupSections,
  isSameValue,
  removeRoleRow,
  resolveControlKind,
  roleOptions,
  roleQuotaColumns,
  roleQuotaDict,
  roleQuotaRows,
  storedFromDraft,
  type FlatDictRow,
  type FormatWords,
  type RoleQuotaRow,
  type SettingDraftValue
} from './settingsLogic'

/**
 * 在线设置中心的纯逻辑：控件分支、差异计算、角色额度表增删行。
 *
 * 只测「算得对不对」，不测组件渲染。
 */

const WORDS: FormatWords = { on: '开', off: '关', empty: '（空）' }

function makeItem(overrides: Partial<SystemSettingItem> = {}): SystemSettingItem {
  return {
    key: 'ai.rate_limit_per_minute',
    value: 60,
    type: 'int',
    group: 'ai',
    label: '每分钟请求上限',
    description: '单用户每分钟最多发起多少次 AI 请求',
    min: 1,
    max: 600,
    options: null,
    updated_at: null,
    updated_by: null,
    ...overrides
  }
}

describe('类型 → 控件分支', () => {
  it('int 用数字控件，bool 用开关，str 用文本框', () => {
    expect(resolveControlKind(makeItem({ type: 'int' }))).toBe('number')
    expect(resolveControlKind(makeItem({ type: 'bool' }))).toBe('switch')
    expect(resolveControlKind(makeItem({ type: 'str' }))).toBe('text')
  })

  it('list 用多选，dict 用角色额度表', () => {
    expect(resolveControlKind(makeItem({ type: 'list' }))).toBe('multiselect')
    expect(resolveControlKind(makeItem({ type: 'dict' }))).toBe('roleQuota')
  })

  it('未知类型按文本处理，不让页面崩', () => {
    const item = makeItem({ type: 'unknown' as SystemSettingItem['type'] })
    expect(resolveControlKind(item)).toBe('text')
  })

  it('list 有候选值时不允许自填，没有候选值时允许', () => {
    expect(allowsFreeEntry(makeItem({ type: 'list', options: ['admin'] }))).toBe(false)
    expect(allowsFreeEntry(makeItem({ type: 'list', options: null }))).toBe(true)
    expect(allowsFreeEntry(makeItem({ type: 'int' }))).toBe(false)
  })

  it('角色候选值优先用后端给的 options', () => {
    expect(roleOptions(makeItem({ type: 'dict', options: ['admin', 'teacher'] }))).toEqual([
      'admin',
      'teacher'
    ])
    expect(roleOptions(makeItem({ type: 'dict', options: null }))).toContain('admin')
  })
})

describe('草稿与存储值的互转', () => {
  it('int 的草稿是数字，存回去还是数字', () => {
    const item = makeItem()
    const draft = draftFromValue(item, 60)
    expect(draft).toBe(60)
    expect(storedFromDraft(item, draft)).toBe(60)
  })

  it('list 的草稿是字符串数组', () => {
    const item = makeItem({ type: 'list', value: ['admin', 'student'] })
    const draft = draftFromValue(item, item.value)
    expect(draft).toEqual(['admin', 'student'])
    expect(storedFromDraft(item, draft)).toEqual(['admin', 'student'])
  })

  it('dict 的草稿是行数组，存回去是对象', () => {
    const item = makeItem({
      type: 'dict',
      value: { admin: { ocr: 100, chat: 200 } }
    })
    const draft = draftFromValue(item, item.value)
    expect(draft).toEqual([{ role: 'admin', limits: { ocr: 100, chat: 200 } }])
    expect(storedFromDraft(item, draft)).toEqual({ admin: { ocr: 100, chat: 200 } })
  })

  it('缺字段时给中性占位，不回退到硬编码常量', () => {
    const item = makeItem({ type: 'str', value: null })
    expect(draftFromValue(item, null)).toBe('')
    expect(draftFromValue(makeItem({ type: 'list', value: null }), null)).toEqual([])
    expect(draftFromValue(makeItem({ type: 'dict', value: null }), null)).toEqual([])
  })
})

describe('差异计算', () => {
  it('没有改动时没有差异', () => {
    const item = makeItem()
    const drafts = { [item.key]: draftFromValue(item, item.value) }
    expect(computeChanges([item], drafts)).toEqual([])
    expect(changedKeys([item], drafts)).toEqual([])
  })

  it('改了值才进差异，旧值与新值都在', () => {
    const item = makeItem()
    const drafts = { [item.key]: 120 }
    const changes = computeChanges([item], drafts)
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({ key: item.key, label: item.label, from: 60, to: 120 })
  })

  it('多选是集合：顺序不同不算改动', () => {
    const item = makeItem({ type: 'list', value: ['admin', 'student'] })
    const same = { [item.key]: ['student', 'admin'] }
    expect(computeChanges([item], same)).toEqual([])
    const different = { [item.key]: ['student'] }
    expect(changedKeys([item], different)).toEqual([item.key])
  })

  it('只提交改动的那几个键，没改的键不进请求体', () => {
    const a = makeItem()
    const b = makeItem({ key: 'ai.context_limit', label: '上下文长度', value: 200000 })
    const c = makeItem({ key: 'ops.log_retention_days', label: '日志保留天数', value: 30 })
    const drafts: Record<string, SettingDraftValue> = {
      [a.key]: 120,
      [b.key]: draftFromValue(b, b.value),
      [c.key]: 90
    }
    const changes = computeChanges([a, b, c], drafts)
    expect(changes.map((change) => change.key)).toEqual([a.key, c.key])
    const payload = changes.map((change) => ({ key: change.key, value: change.to }))
    expect(payload).toEqual([
      { key: 'ai.rate_limit_per_minute', value: 120 },
      { key: 'ops.log_retention_days', value: 90 }
    ])
  })

  it('dict 的增删角色行会进差异', () => {
    const item = makeItem({ type: 'dict', value: { admin: { ocr: 100 } } })
    const rows = roleQuotaRows(item.value) as RoleQuotaRow[]
    const drafts = { [item.key]: addRoleRow(rows, ['ocr'], 'teacher') }
    const changes = computeChanges([item], drafts)
    expect(changes).toHaveLength(1)
    expect(changes[0].to).toEqual({ admin: { ocr: 100 }, teacher: { ocr: 0 } })
  })
})

describe('与默认值对比', () => {
  it('当前值等于默认值时不标记', () => {
    const item = makeItem({ value: 60 })
    expect(differsFromDefault(item, { [item.key]: 60 })).toBe(false)
  })

  it('当前值偏离默认值时标记', () => {
    const item = makeItem({ value: 120 })
    expect(differsFromDefault(item, { [item.key]: 60 })).toBe(true)
  })

  it('默认值表里没有这个键时不标记', () => {
    const item = makeItem({ value: 120 })
    expect(differsFromDefault(item, {})).toBe(false)
  })
})

describe('角色额度表的增删行', () => {
  const rows: RoleQuotaRow[] = [
    { role: 'admin', limits: { ocr: 100, chat: 200 } },
    { role: 'student', limits: { ocr: 10, chat: 20 } }
  ]

  it('加行按当前列铺 0，不影响已有行', () => {
    const next = addRoleRow(rows, ['ocr', 'chat'])
    expect(next).toHaveLength(3)
    expect(next[2]).toEqual({ role: '', limits: { ocr: 0, chat: 0 } })
    expect(rows).toHaveLength(2)
  })

  it('加行可以带上角色名', () => {
    expect(addRoleRow(rows, ['ocr'], 'teacher')[2]).toEqual({
      role: 'teacher',
      limits: { ocr: 0 }
    })
  })

  it('删行按序号删，越界原样返回', () => {
    const next = removeRoleRow(rows, 0)
    expect(next.map((row) => row.role)).toEqual(['student'])
    expect(removeRoleRow(rows, 9)).toBe(rows)
    expect(removeRoleRow(rows, -1)).toBe(rows)
  })

  it('列按 ocr / chat 在前，其余按出现顺序', () => {
    expect(roleQuotaColumns(rows)).toEqual(['ocr', 'chat'])
    expect(roleQuotaColumns([{ role: 'x', limits: { extra: 1, chat: 2, ocr: 3 } }])).toEqual([
      'ocr',
      'chat',
      'extra'
    ])
  })

  it('角色名为空的行不进存储值', () => {
    expect(
      roleQuotaDict([
        { role: 'admin', limits: { ocr: 1 } },
        { role: '  ', limits: { ocr: 2 } }
      ])
    ).toEqual({ admin: { ocr: 1 } })
  })
})

describe('扁平 dict（按值形状分派）', () => {
  // 契约类型里 dict 只声明了角色额度那一种形状，扁平 dict 在运行时真实存在，
  // 这里用 helper 造值，避免每处都写 as。
  function flatValue(value: Record<string, number>): SystemSettingItem['value'] {
    return value as unknown as SystemSettingItem['value']
  }

  // 后端 /api/system/settings 的真实形状：{ ocr: 10, chat: 20 }
  const flat = makeItem({
    key: 'ai.rate_limit_per_minute',
    type: 'dict',
    value: flatValue({ ocr: 10, chat: 20 }),
    min: 0,
    max: 1_000_000
  })

  it('值全是数字的 dict 识别为扁平', () => {
    expect(dictShape(flat.value)).toBe('flat')
    expect(resolveControlKind(flat)).toBe('flatDict')
  })

  it('值是对象的 dict 仍走角色额度表', () => {
    const nested = makeItem({ type: 'dict', value: { admin: { ocr: 500, chat: 1_000_000 } } })
    expect(dictShape(nested.value)).toBe('nested')
    expect(resolveControlKind(nested)).toBe('roleQuota')
  })

  it('空对象或非对象回落角色额度表，保留添加角色入口', () => {
    expect(dictShape({})).toBe('nested')
    expect(dictShape(null)).toBe('nested')
    expect(dictShape(60)).toBe('nested')
    expect(resolveControlKind(makeItem({ type: 'dict', value: {} }))).toBe('roleQuota')
  })

  it('草稿是键值行，键名与值都来自存储值', () => {
    const draft = draftFromValue(flat, flat.value) as FlatDictRow[]
    expect(draft).toEqual([
      { key: 'ocr', value: 10 },
      { key: 'chat', value: 20 }
    ])
    expect(storedFromDraft(flat, draft)).toEqual({ ocr: 10, chat: 20 })
  })

  it('键清单优先按后端 options 排序，但不凭空造键', () => {
    const ordered = makeItem({
      type: 'dict',
      value: flatValue({ chat: 20, ocr: 10 }),
      options: ['ocr', 'chat']
    })
    expect(flatDictKeys(ordered)).toEqual(['ocr', 'chat'])
    // 后端列了两种能力，但值里只有一种：只画有值的，避免给未知能力写 0
    const partial = makeItem({ type: 'dict', value: flatValue({ chat: 20 }), options: ['ocr', 'chat'] })
    expect(flatDictKeys(partial)).toEqual(['chat'])
    // 后端没给清单时按值里的键序
    expect(flatDictKeys(flat)).toEqual(['ocr', 'chat'])
    expect(flatDictRows(flat)).toEqual([
      { key: 'ocr', value: 10 },
      { key: 'chat', value: 20 }
    ])
  })

  it('改值后只提交改动的键，其余键原样带上', () => {
    const draft = draftFromValue(flat, flat.value) as FlatDictRow[]
    draft[1]!.value = 50
    const changes = computeChanges([flat], { [flat.key]: draft })
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({ key: flat.key })
    expect(changes[0]!.to).toEqual({ ocr: 10, chat: 50 })
  })

  it('没改动时不进差异', () => {
    const draft = draftFromValue(flat, flat.value)
    expect(computeChanges([flat], { [flat.key]: draft })).toEqual([])
  })

  it('提交值取整，下界取后端给的 min', () => {
    expect(flatDictValue([{ key: 'ocr', value: 10.7 }])).toEqual({ ocr: 10 })
    expect(flatDictValue([{ key: ' ', value: 1 }])).toEqual({})
    expect(flatDictMin(flat)).toBe(0)
    expect(flatDictMin(makeItem({ type: 'dict', min: null }))).toBe(0)
    expect(flatDictMin(makeItem({ type: 'dict', min: 5 }))).toBe(5)
  })

  it('展示文案是「键 值」，不是角色额度那套「角色：能力 值」', () => {
    expect(formatSettingValue(flatValue({ ocr: 10, chat: 20 }), WORDS)).toBe('ocr 10，chat 20')
  })
})

describe('分组与展示', () => {
  it('ai / ops 固定在前，其余分组补在后面', () => {
    const sections = groupSections([
      makeItem({ key: 'ops.a', group: 'ops' }),
      // 契约只写了 ai / ops，这里刻意塞一个未知分组，验证页面不会因此崩掉
      makeItem({ key: 'other.a', group: 'other' as SystemSettingItem['group'] }),
      makeItem({ key: 'ai.a', group: 'ai' })
    ])
    expect(sections.map((section) => section.group)).toEqual(['ai', 'ops', 'other'])
    expect(sections[0].items.map((item) => item.key)).toEqual(['ai.a'])
  })

  it('取值的展示文案区分布尔与空值', () => {
    expect(formatSettingValue(true, WORDS)).toBe('开')
    expect(formatSettingValue(false, WORDS)).toBe('关')
    expect(formatSettingValue(null, WORDS)).toBe('（空）')
    expect(formatSettingValue('', WORDS)).toBe('（空）')
    expect(formatSettingValue(60, WORDS)).toBe('60')
    expect(formatSettingValue(['admin', 'student'], WORDS)).toBe('admin、student')
    expect(formatSettingValue({ admin: { ocr: 10 } }, WORDS)).toBe('admin：ocr 10')
  })
})

describe('深比较', () => {
  it('对象键顺序不同也算同一个值', () => {
    expect(isSameValue({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true)
  })

  it('数组顺序不同算不同值（多选另有集合语义）', () => {
    expect(isSameValue(['a', 'b'], ['b', 'a'])).toBe(false)
  })
})

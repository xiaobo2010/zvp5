import type {
  AiAgentEvent,
  AiAgentEventStatus,
  AiAgentPreview,
  AiAgentProposal,
  AiAgentStopReason,
  AiAgentUsage
} from '@/../types/ai'
import { reactive } from 'vue'
import type { ChatTurn } from './chatSession'

/**
 * 智能体模式的展示模型。
 *
 * 与 chatSession 同层、只管「后端返回的留痕怎么落到界面」：
 * 事件与提案在 turn 上挂视图字段（展开态、处理中/终态），纯函数放在这里，
 * 组件里只剩 v-if 与 class 绑定，单测不必挂载组件。
 */

/**
 * 工具白名单：只用于把名字翻成中文标签。
 *
 * 与后端封闭注册表逐条对齐（`services/agent/tools.py` + `ops_tools` +
 * `tools_platform` + `tools_governance` + `tools_ops_extra` + `tools_scripts`，共
 * 57 条；`platform_help` 走 `TOOL_LABEL_KEYS` 覆盖，不在此列）。
 *
 * 这张表**不是权限判据**（权限在服务端），漏一条的后果只是界面显示「未知工具」；
 * 所以这里宁可列全，也不要靠「名字看着像就通配」——那会把模型编的名字也翻成
 * 一个看似正常的标签，审计时反而分不清。
 */
const KNOWN_TOOLS = [
  // 活动域（10）
  'search_activities',
  'list_activities',
  'get_activity',
  'activity_statistics',
  'activity_types_summary',
  'list_activity_members',
  'user_volunteer_time',
  'user_activities',
  'list_groups',
  'update_member_duration',
  'set_member_status',
  // 文件 / 数据库 / SSH（17）
  'fs_list',
  'fs_stat',
  'fs_read',
  'fs_write',
  'fs_mkdir',
  'fs_rename',
  'fs_delete',
  'fs_chmod',
  'db_schema',
  'db_columns',
  'db_rows',
  'db_query',
  'db_insert',
  'db_update',
  'db_delete',
  'db_ddl',
  'ssh_exec',
  // 平台运维（8）
  'platform_logs',
  'db_diagnostics',
  'backup_status',
  'scheduler_status',
  'device_ban_list',
  'user_ban',
  'user_unban',
  'device_unban',
  // 治理（9）
  'ai_call_logs',
  'ai_gateway_status',
  'ai_prompt_audit_list',
  'ai_quota_set',
  'ai_api_key_revoke',
  'ban_history',
  'user_lookup',
  'maintenance_mode',
  'notify_send',
  // 运维补充（10）
  'agent_queue_status',
  'server_time',
  'settings_snapshot',
  'volunteer_audit',
  'scheduler_jobs',
  'scheduler_control',
  'attachment_stats',
  'attachment_gc_preview',
  'attachment_gc_run',
  'backup_run',
  // 白名单脚本（2）
  'script_catalog',
  'script_run'
]

const KNOWN_TOOL_SET = new Set(KNOWN_TOOLS)

/**
 * 平台使用帮助：所有角色都可见（学生会也要查「这个功能怎么用」）。
 *
 * 它不在 KNOWN_TOOLS 里——那张表是与后端注册表对齐的 57 条，这一条是独立的
 * 只读帮助入口，标签键也不走 snake_case 通配（`platformHelp`，与后端别名
 * `platformHelp → platform_help` 对应）。
 */
export const PLATFORM_HELP_TOOL = 'platform_help'

/**
 * 义工管理域工具：非 admin（学生会）能看到的声明集合。
 *
 * 与后端 department 角色的工具白名单一致（11 条），顺序照后端声明；
 * 运维工具（文件 / 数据库 / SSH）不在其中。
 */
export const VOLUNTEER_AGENT_TOOLS = [
  'search_activities',
  'list_activities',
  'get_activity',
  'activity_statistics',
  'activity_types_summary',
  'list_activity_members',
  'user_volunteer_time',
  'user_activities',
  'list_groups',
  'update_member_duration',
  'set_member_status'
] as const

/** admin 的可见集合：注册表里的 57 条加平台帮助。 */
const ALL_AGENT_TOOLS = [...KNOWN_TOOLS, PLATFORM_HELP_TOOL]

/**
 * 当前角色能看到的工具声明。
 *
 * 声明层过滤的意义是「不让模型看见它无权调用的工具」，执行期仍由后端二次校验；
 * 前端只用它决定展示与提示，不作为权限判据。
 */
export function availableAgentTools(position: string[]): string[] {
  if (position.includes('admin')) {
    return [...ALL_AGENT_TOOLS]
  }
  return [...VOLUNTEER_AGENT_TOOLS, PLATFORM_HELP_TOOL]
}

/**
 * 解锁状态：模块级单例，跨组件共享。
 *
 * 授权窗口由服务端给到期时间，前端只负责「到点自动失效」与展示剩余分钟；
 * 不放 localStorage——解锁是本次访问的凭证，刷新页面重新验证更符合预期。
 */
export const agentUnlock = reactive<{ expiresAt: number | null }>({ expiresAt: null })

/** 当前是否在授权窗口内；`now` 只给测试与倒计时用。 */
export function isAgentUnlocked(now: number = Date.now()): boolean {
  return agentUnlock.expiresAt !== null && agentUnlock.expiresAt > now
}

/**
 * 记下服务端给的到期时间。
 *
 * 解析不了（空串、垃圾值）就落成未解锁，不拿硬编码的 30 分钟兜底：
 * 窗口长度只认服务端这一个来源。
 */
export function setAgentUnlockedUntil(iso: string): number | null {
  const deadline = parseUnlockDeadline(iso)
  agentUnlock.expiresAt = deadline
  return deadline
}

/** 主动清掉解锁状态（例如服务端回「需要解锁」）。 */
export function clearAgentUnlock(): void {
  agentUnlock.expiresAt = null
}

/** 授权窗口剩余分钟（向上取整）；未解锁或已过期返回 0。 */
export function agentUnlockMinutesLeft(now: number = Date.now()): number {
  const deadline = agentUnlock.expiresAt
  if (deadline === null) {
    return 0
  }
  const left = deadline - now
  if (left <= 0) {
    return 0
  }
  return Math.max(1, Math.ceil(left / 60_000))
}

function parseUnlockDeadline(value: string): number | null {
  const text = value.trim()
  if (!text) {
    return null
  }
  const iso = Date.parse(text)
  if (Number.isFinite(iso)) {
    return iso
  }
  return parseServerTime(text)
}

/**
 * 运维写工具：确认时要另一位管理员的 6 位动态码。
 *
 * 其余写工具不在其中，`requires_dual_auth` 由服务端下发：
 * 活动域两条（update_member_duration / set_member_status）与平台治理四条
 * （notify_send / ai_quota_set / ai_api_key_revoke / attachment_gc_run）
 * 都只需本人确认。
 */
const OPS_WRITE_TOOLS = new Set([
  'fs_write',
  'fs_mkdir',
  'fs_rename',
  'fs_delete',
  'fs_chmod',
  'db_insert',
  'db_update',
  'db_delete',
  'db_ddl',
  'ssh_exec'
])

/** 提案卡的状态机：pending → confirming/rejecting → confirmed/rejected/expired/error。 */
export type AgentProposalState =
  | 'pending'
  | 'confirming'
  | 'rejecting'
  | 'confirmed'
  | 'rejected'
  | 'expired'
  | 'error'

/** 一条工具留痕的视图（多一个展开态）。 */
export interface AgentEventView extends AiAgentEvent {
  expanded: boolean
}

/** 一张提案卡的视图（多处理状态、结果/错误与结构化预览）。 */
export interface AgentProposalCard {
  oid: string
  toolName: string
  argsSummary: string
  /** 结构化预览；后端没给或形状不认识时为 null（退回参数摘要）。 */
  preview: AiAgentPreview | null
  /** 确认时是否需要另一位管理员的动态码。 */
  requiresDualAuth: boolean
  /** 30 分钟双人授权窗口是否已命中（命中时确认不再要码）。 */
  dualAuthCached: boolean
  expiresAt: string
  state: AgentProposalState
  resultSummary?: string
  errorCode?: string
  errorTitle?: string
  errorHint?: string
  requestId?: string | null
}

/** 对话页的一轮：对话模式只用到基类字段，智能体模式多挂事件与提案。 */
export type PageTurn = ChatTurn & {
  mode?: 'chat' | 'agent'
  events?: AgentEventView[]
  proposals?: AgentProposalCard[]
  /** run 的终止原因（触顶/超时），用于顶部提示而非报错。 */
  stopReason?: AiAgentStopReason | null
  /** run 的轮数与 token 用量。 */
  rounds?: number
  usage?: AiAgentUsage | null
  /** 回看一次历史 run 的事件。 */
  replay?: AgentEventView[] | null
  replayLoading?: boolean
  replayFailed?: boolean
}

/** 标签键覆盖：名字翻译不走 `tools.<snake_case>` 通配的那几条。 */
const TOOL_LABEL_KEYS: Record<string, string> = {
  [PLATFORM_HELP_TOOL]: 'ai.agent.tools.platformHelp'
}

/** 工具名 → i18n 标签键；白名单外的名字（含 null）回落到「未知工具」。 */
export function toolLabelKey(name: string | null | undefined): string {
  if (name) {
    const override = TOOL_LABEL_KEYS[name]
    if (override) {
      return override
    }
    if (KNOWN_TOOL_SET.has(name)) {
      return `ai.agent.tools.${name}`
    }
  }
  return 'ai.agent.tools.unknown'
}

/** 状态徽标语义：执行成功绿、待确认蓝、被拒不橙、失败红。 */
export function eventStatusTone(status: AiAgentEventStatus): string {
  switch (status) {
    case 'executed':
      return 'is-success'
    case 'proposed':
      return 'is-info'
    case 'denied':
      return 'is-warning'
    case 'failed':
      return 'is-danger'
    default:
      return ''
  }
}

/** 提案卡右侧徽标语义（pending 时不显示徽标，改显示有效期文案）。 */
export function proposalTone(state: AgentProposalState): string {
  switch (state) {
    case 'confirming':
    case 'rejecting':
      return 'is-info'
    case 'confirmed':
      return 'is-success'
    case 'expired':
      return 'is-warning'
    case 'error':
      return 'is-danger'
    default:
      return ''
  }
}

/**
 * 服务端时间的解析。
 *
 * `expires_at` 是 `2026-10-25 12:10:00`（本地时间、空格分隔），不是 ISO：
 * `new Date('2026-10-25 12:10:00')` 在部分浏览器里得到 Invalid Date，所以按
 * 数字逐段构造。解析不了返回 null，由调用方回落到固定文案。
 */
export function parseServerTime(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(value.trim())
  if (!match) {
    return null
  }
  const [, year, month, day, hour, minute, second] = match
  const time = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  ).getTime()
  return Number.isFinite(time) ? time : null
}

/** 剩余有效分钟数（向上取整）；已过期返回 0，解析不了返回 null。 */
export function proposalMinutesLeft(expiresAt: string, now: number): number | null {
  const deadline = parseServerTime(expiresAt)
  if (deadline === null) {
    return null
  }
  const left = deadline - now
  if (left <= 0) {
    return 0
  }
  return Math.max(1, Math.ceil(left / 60_000))
}

function toStatus(value: unknown): AiAgentEventStatus {
  return value === 'executed' || value === 'proposed' || value === 'denied' || value === 'failed'
    ? value
    : 'failed'
}

function toKind(value: unknown): AiAgentEvent['tool_kind'] {
  return value === 'read' || value === 'write' ? value : null
}

function readString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function readRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === 'string')
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 事件数组的防御性收敛：形状不认识的行直接丢弃，不让一条脏数据打挂整页。 */
export function normalizeEventViews(raw: unknown): AgentEventView[] {
  if (!Array.isArray(raw)) {
    return []
  }
  const views: AgentEventView[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') {
      continue
    }
    const record = item as Partial<AiAgentEvent>
    views.push({
      round_no: typeof record.round_no === 'number' ? record.round_no : 0,
      tool_name: typeof record.tool_name === 'string' ? record.tool_name : null,
      tool_kind: toKind(record.tool_kind),
      args_summary: typeof record.args_summary === 'string' ? record.args_summary : null,
      result_summary: typeof record.result_summary === 'string' ? record.result_summary : '',
      status: toStatus(record.status),
      expanded: false
    })
  }
  return views
}

/**
 * 预览归一化。
 *
 * 后端按 kind 给出结构化预览；形状不认识（后端加了新 kind）或必填字段缺失
 * 的一律丢弃（返回 null），让界面退回参数摘要，而不是渲染半截数据。
 */
export function normalizePreview(raw: unknown): AiAgentPreview | null {
  const record = readRecord(raw)
  if (!record) {
    return null
  }
  switch (readString(record.kind)) {
    case 'diff': {
      const path = readString(record.path)
      const diff = readString(record.diff)
      if (path === null || diff === null) return null
      return { kind: 'diff', path, diff, truncated: record.truncated === true }
    }
    case 'new_file': {
      const path = readString(record.path)
      const content = readString(record.content)
      if (path === null || content === null) return null
      return { kind: 'new_file', path, content, truncated: record.truncated === true }
    }
    case 'mkdir': {
      const path = readString(record.path)
      if (path === null) return null
      return { kind: 'mkdir', path }
    }
    case 'rename': {
      const from = readString(record.from)
      const to = readString(record.to)
      if (from === null || to === null) return null
      return { kind: 'rename', from, to }
    }
    case 'delete': {
      const path = readString(record.path)
      if (path === null) return null
      return {
        kind: 'delete',
        path,
        entries: readStringArray(record.entries),
        entries_truncated: record.entries_truncated === true
      }
    }
    case 'chmod': {
      const path = readString(record.path)
      const from = readString(record.from)
      const to = readString(record.to)
      if (path === null || from === null || to === null) return null
      return { kind: 'chmod', path, from, to }
    }
    case 'row_insert': {
      const table = readString(record.table)
      if (table === null) return null
      return {
        kind: 'row_insert',
        table,
        columns: readStringArray(record.columns),
        values: readRecord(record.values) ?? {}
      }
    }
    case 'row_update': {
      const table = readString(record.table)
      if (table === null) return null
      return {
        kind: 'row_update',
        table,
        key: readRecord(record.key) ?? {},
        values: readRecord(record.values) ?? {}
      }
    }
    case 'row_delete': {
      const table = readString(record.table)
      if (table === null) return null
      return { kind: 'row_delete', table, key: readRecord(record.key) ?? {} }
    }
    case 'sql': {
      const sql = readString(record.sql)
      if (sql === null) return null
      return {
        kind: 'sql',
        sql,
        summary: readString(record.summary) ?? '',
        verb: readString(record.verb) ?? '',
        target: readString(record.target) ?? '',
        destructive: record.destructive === true
      }
    }
    case 'command': {
      const command = readString(record.command)
      if (command === null) return null
      return {
        kind: 'command',
        target: readString(record.target) ?? '',
        command
      }
    }
    case 'args': {
      const args = readRecord(record.args)
      if (!args) return null
      return { kind: 'args', args }
    }
    default:
      return null
  }
}

/** 提案数组 → 卡片（初始都是待确认）；缺 oid 的行丢掉，否则按钮没有句柄可调。 */
export function normalizeProposals(raw: unknown): AgentProposalCard[] {
  if (!Array.isArray(raw)) {
    return []
  }
  const cards: AgentProposalCard[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') {
      continue
    }
    const record = item as Partial<AiAgentProposal>
    if (typeof record.oid !== 'string' || !record.oid) {
      continue
    }
    cards.push({
      oid: record.oid,
      toolName: typeof record.tool_name === 'string' ? record.tool_name : '',
      argsSummary: typeof record.args_summary === 'string' ? record.args_summary : '',
      preview: normalizePreview(record.preview),
      requiresDualAuth: record.requires_dual_auth === true,
      dualAuthCached: record.dual_auth_cached === true,
      expiresAt: typeof record.expires_at === 'string' ? record.expires_at : '',
      state: 'pending'
    })
  }
  return cards
}

/** 是不是运维写工具（确认要双人动态码的那一类）。 */
export function isOpsWriteTool(name: string | null | undefined): boolean {
  return typeof name === 'string' && OPS_WRITE_TOOLS.has(name)
}

/**
 * 把列表里待确认的运维写卡片标记为「已授权」。
 *
 * 30 分钟窗口内，同一位 operator 的后续写操作不再重复要码；
 * 返回被标记的卡片数，方便调用方提示。
 */
export function markDualAuthCached(cards: AgentProposalCard[]): number {
  let count = 0
  for (const card of cards) {
    if (card.state === 'pending' && card.requiresDualAuth && isOpsWriteTool(card.toolName)) {
      card.dualAuthCached = true
      count += 1
    }
  }
  return count
}

/** 预览块的语义色：删除类红、命令/改权限橙、其余蓝。 */
export function previewTone(preview: AiAgentPreview | null): string {
  if (!preview) {
    return ''
  }
  switch (preview.kind) {
    case 'delete':
    case 'row_delete':
      return 'is-danger'
    case 'sql':
      return preview.destructive ? 'is-danger' : 'is-warning'
    case 'command':
    case 'chmod':
    case 'rename':
      return 'is-warning'
    default:
      return 'is-info'
  }
}

/** 预览块的标题文案键；没有预览时给「参数摘要」。 */
export function previewTitleKey(preview: AiAgentPreview | null): string {
  if (!preview) {
    return 'ai.agent.preview.none'
  }
  return `ai.agent.preview.${preview.kind}`
}

/** 预览作用对象（路径 / 表名 / 主机）：给标题旁的一行摘要用。 */
export function previewTarget(preview: AiAgentPreview | null): string {
  if (!preview) {
    return ''
  }
  switch (preview.kind) {
    case 'diff':
    case 'new_file':
    case 'mkdir':
    case 'delete':
    case 'chmod':
      return preview.path
    case 'rename':
      return preview.from
    case 'row_insert':
    case 'row_update':
    case 'row_delete':
      return preview.table
    case 'sql':
    case 'command':
      return preview.target
    default:
      return ''
  }
}

/** diff 的行分类：新增 / 删除 / 上下文 / 块头。 */
export type AgentDiffLineType = 'add' | 'remove' | 'context' | 'hunk'

export interface AgentDiffLine {
  type: AgentDiffLineType
  text: string
}

/**
 * 统一 diff 拆成带类型的行。
 *
 * 保留每行的原始文本（含 +/- 前缀），界面按 type 上色即可；
 * `---` / `+++` 两行文件头跳过——路径已经单独显示。
 */
export function parseDiffLines(diff: string): AgentDiffLine[] {
  if (!diff) {
    return []
  }
  const lines: AgentDiffLine[] = []
  const body = diff.replace(/\n+$/, '')
  for (const raw of body.split('\n')) {
    if (raw.startsWith('---') || raw.startsWith('+++')) {
      continue
    }
    if (raw.startsWith('@@')) {
      lines.push({ type: 'hunk', text: raw })
      continue
    }
    if (raw.startsWith('+')) {
      lines.push({ type: 'add', text: raw })
      continue
    }
    if (raw.startsWith('-')) {
      lines.push({ type: 'remove', text: raw })
      continue
    }
    lines.push({ type: 'context', text: raw })
  }
  return lines
}

/** 预览里的一行键值（行变更类预览用）。 */
export interface AgentPreviewEntry {
  label: string
  value: string
  /** key = 主键条件，value = 待写入的列。 */
  group: 'key' | 'value'
}

/** 预览里的值转成可读文本：字符串原样，其余 JSON 化，空值给占位符。 */
export function previewValueText(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value) ?? '—'
  } catch {
    return '—'
  }
}

/** 行变更类预览的键值表；其他 kind 返回空数组。 */
export function previewEntries(preview: AiAgentPreview | null): AgentPreviewEntry[] {
  if (!preview) {
    return []
  }
  if (preview.kind === 'row_insert') {
    const columns = Array.from(new Set([...preview.columns, ...Object.keys(preview.values)]))
    return columns.map((column) => ({
      label: column,
      value: previewValueText(preview.values[column]),
      group: 'value' as const
    }))
  }
  if (preview.kind === 'row_update') {
    const key = Object.entries(preview.key).map(([label, value]) => ({
      label,
      value: previewValueText(value),
      group: 'key' as const
    }))
    const values = Object.entries(preview.values).map(([label, value]) => ({
      label,
      value: previewValueText(value),
      group: 'value' as const
    }))
    return [...key, ...values]
  }
  if (preview.kind === 'row_delete') {
    return Object.entries(preview.key).map(([label, value]) => ({
      label,
      value: previewValueText(value),
      group: 'key' as const
    }))
  }
  return []
}

/** 新建文件预览只展示前若干行正文。 */
export const PREVIEW_HEAD_LINES = 20

export interface AgentContentHead {
  lines: string[]
  total: number
  truncated: boolean
}

/** 取正文前 N 行（并告诉调用方是否被截断）。 */
export function previewHeadLines(content: string, limit = PREVIEW_HEAD_LINES): AgentContentHead {
  const all = content.length === 0 ? [] : content.split('\n')
  return {
    lines: all.slice(0, Math.max(0, limit)),
    total: all.length,
    truncated: all.length > Math.max(0, limit)
  }
}

/**
 * 预览的视图模型：把判别联合摊平成「每种形态各带自己要显示的字段」。
 *
 * 组件里只写 `v-if="view.kind === 'diff'"` 这类分支，不再到处做类型收窄；
 * 归一化（丢弃不认识的 kind）已经在 normalizePreview 做过一次。
 */
export type AgentPreviewView =
  | { kind: 'none' }
  | { kind: 'diff'; target: string; lines: AgentDiffLine[]; serverTruncated: boolean }
  | {
      kind: 'new_file'
      target: string
      lines: string[]
      total: number
      headTruncated: boolean
      serverTruncated: boolean
    }
  | { kind: 'mkdir'; target: string }
  | { kind: 'rename'; target: string; from: string; to: string }
  | { kind: 'delete'; target: string; entries: string[]; entriesTruncated: boolean }
  | { kind: 'chmod'; target: string; from: string; to: string }
  | { kind: 'row'; target: string; entries: AgentPreviewEntry[] }
  | {
      kind: 'sql'
      target: string
      sql: string
      summary: string
      verb: string
      destructive: boolean
    }
  | { kind: 'command'; target: string; command: string }
  | { kind: 'args'; target: string; text: string }

/** 预览 → 视图模型；没有预览时给 kind='none'。 */
export function buildPreviewView(preview: AiAgentPreview | null): AgentPreviewView {
  if (!preview) {
    return { kind: 'none' }
  }
  switch (preview.kind) {
    case 'diff':
      return {
        kind: 'diff',
        target: preview.path,
        lines: parseDiffLines(preview.diff),
        serverTruncated: preview.truncated === true
      }
    case 'new_file': {
      const head = previewHeadLines(preview.content)
      return {
        kind: 'new_file',
        target: preview.path,
        lines: head.lines,
        total: head.total,
        headTruncated: head.truncated,
        serverTruncated: preview.truncated === true
      }
    }
    case 'mkdir':
      return { kind: 'mkdir', target: preview.path }
    case 'rename':
      return { kind: 'rename', target: preview.from, from: preview.from, to: preview.to }
    case 'delete':
      return {
        kind: 'delete',
        target: preview.path,
        entries: preview.entries,
        entriesTruncated: preview.entries_truncated === true
      }
    case 'chmod':
      return { kind: 'chmod', target: preview.path, from: preview.from, to: preview.to }
    case 'row_insert':
    case 'row_update':
    case 'row_delete':
      return { kind: 'row', target: preview.table, entries: previewEntries(preview) }
    case 'sql':
      return {
        kind: 'sql',
        target: preview.target,
        sql: preview.sql,
        summary: preview.summary,
        verb: preview.verb,
        destructive: preview.destructive
      }
    case 'command':
      return { kind: 'command', target: preview.target, command: preview.command }
    case 'args':
      return { kind: 'args', target: '', text: previewValueText(preview.args) }
    default:
      return { kind: 'none' }
  }
}

/** run 的终止原因归一：不认识的值当作「正常结束」。 */
export function normalizeStopReason(value: unknown): AiAgentStopReason | null {
  return value === 'max_iterations' ||
    value === 'token_budget' ||
    value === 'timeout' ||
    value === 'cancelled'
    ? value
    : null
}

/** 终止原因的文案键。 */
export function stopReasonKey(reason: AiAgentStopReason | null): string {
  if (!reason) {
    return 'ai.agent.stopReason.none'
  }
  return `ai.agent.stopReason.${reason}`
}

/** 终止原因的补充说明文案键。 */
export function stopReasonHintKey(reason: AiAgentStopReason | null): string {
  if (!reason) {
    return 'ai.agent.stopReason.noneHint'
  }
  return `ai.agent.stopReason.${reason}Hint`
}

/** token 用量归一；上游没报时给 null。 */
export function normalizeUsage(raw: unknown): AiAgentUsage | null {
  const record = readRecord(raw)
  if (!record) {
    return null
  }
  return {
    prompt_tokens: readNumber(record.prompt_tokens),
    completion_tokens: readNumber(record.completion_tokens),
    total_tokens: readNumber(record.total_tokens),
    cache_read_tokens: readNumber(record.cache_read_tokens),
    cache_write_tokens: readNumber(record.cache_write_tokens)
  }
}

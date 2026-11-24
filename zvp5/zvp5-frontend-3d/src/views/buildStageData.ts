import type { CanaryActivity, CanaryNotice, CanaryTime, CanaryUser } from '@/api/canary'
import { LOG_TYPE_LABELS, type AdminLog, type HomeAgentRun } from '@/api/home'
import {
  STAGE_MODULES,
  type StageActivity,
  type StageActivityStatus,
  type StageData,
  type StageExtra,
  type StageExtraItem,
  type StageHourPoint,
  type StageHours,
  type StageIdentity
} from '@/types/stage'

export interface StageSource {
  profile: CanaryUser
  className: string
  time: CanaryTime | null
  activities: CanaryActivity[]
  pending: CanaryActivity[]
  agents: HomeAgentRun[]
  logs: AdminLog[]
  notices: CanaryNotice[]
  /** Server clock minus local clock, in milliseconds. */
  serverOffsetMs?: number
}

const ROLE_LABELS: Record<string, string> = {
  student: '学生',
  volunteer: '志愿者',
  admin: '管理员',
  department: '学生会',
  secretary: '秘书',
  auditor: '审计',
  teacher: '教师',
  system: '系统'
}

const TYPE_LABELS: Record<string, string> = {
  'on-campus': '校内活动',
  'off-campus': '校外活动',
  'social-practice': '社会实践',
  hybrid: '混合活动'
}

const STATUS_LABELS: Record<StageActivityStatus, string> = {
  draft: '草稿',
  pending: '待审核',
  refused: '已拒绝',
  effective: '已生效'
}

const AGENT_STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  succeeded: '已完成',
  success: '已完成',
  running: '执行中',
  queued: '排队中',
  failed: '失败',
  error: '失败',
  cancelled: '已取消'
}

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function round1(value: number): number {
  return Number.isFinite(value) ? Number(value.toFixed(1)) : 0
}

function sumHours(time: CanaryTime | null): number {
  if (!time) return 0
  return round1(
    Number(time['on-campus'] || 0) +
      Number(time['off-campus'] || 0) +
      Number(time['social-practice'] || 0)
  )
}

function greetingFor(hour: number): string {
  if (hour < 5) return '夜深了'
  if (hour < 9) return '早上好'
  if (hour < 12) return '上午好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

function dateLabelFor(date: Date): string {
  return `${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAYS[date.getDay()]}`
}

function formatActivityDate(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(value || '')
  if (!match) return value || '—'
  return match[2] ? `${match[1].slice(5)} ${match[2]}` : match[1]
}

function formatIso(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatMs(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return formatIso(new Date(value).toISOString())
}

function stripMarkdown(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#>*_~|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function clip(value: string, max: number): string {
  const text = value.trim()
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 1))}…`
}

function normalizeStatus(value: string | undefined): StageActivityStatus {
  if (value === 'draft' || value === 'refused' || value === 'effective') return value
  return 'pending'
}

export function mapActivity(activity: CanaryActivity): StageActivity {
  const mine = activity.mine
  const typeKey = (mine?.mode ?? activity.type) as StageActivity['typeKey']
  const status = normalizeStatus(mine?.status ?? activity.status)
  return {
    id: mine?._id || activity._id,
    name: activity.name || '未命名活动',
    dateLabel: formatActivityDate(activity.date),
    place: activity.place || '地点未填写',
    typeLabel: TYPE_LABELS[typeKey] ?? '活动',
    typeKey,
    status,
    statusLabel: STATUS_LABELS[status],
    hours: typeof mine?.duration === 'number' ? round1(mine.duration) : null
  }
}

function hourPoints(time: CanaryTime | null): StageHourPoint[] {
  return [
    {
      key: 'on-campus',
      label: '校内时长',
      shortLabel: '校内',
      hours: round1(Number(time?.['on-campus'] || 0))
    },
    {
      key: 'off-campus',
      label: '校外时长',
      shortLabel: '校外',
      hours: round1(Number(time?.['off-campus'] || 0))
    },
    {
      key: 'social-practice',
      label: '社会实践',
      shortLabel: '实践',
      hours: round1(Number(time?.['social-practice'] || 0))
    }
  ]
}

function agentItem(run: HomeAgentRun): StageExtraItem {
  const status = AGENT_STATUS_LABELS[run.status] ?? run.status ?? '执行'
  const result = run.result ? stripMarkdown(run.result) : ''
  const title = result
    ? clip(result, 46)
    : run.error_message
      ? clip(stripMarkdown(run.error_message), 46)
      : 'ZVAgent 执行记录'
  return {
    id: `agent-${run.oid}`,
    kind: 'agent',
    title,
    meta: `${status} · ${formatIso(run.created_at)}`,
    detail: run.model ? `模型 ${run.model}` : '未记录模型',
    tone: 'violet'
  }
}

function logItem(log: AdminLog): StageExtraItem {
  return {
    id: `log-${log._id}`,
    kind: 'log',
    title: LOG_TYPE_LABELS[log.type ?? ''] ?? '风险日志',
    meta: `${formatMs(log.timestamp)}${log.user_name ? ` · ${log.user_name}` : ''}`,
    detail: clip(log.data || log.url || '无详细信息', 64),
    tone: 'rose'
  }
}

function reviewItem(activity: CanaryActivity): StageExtraItem {
  const mode = activity.mine?.mode ?? activity.type
  return {
    id: `review-${activity._id}`,
    kind: 'review',
    title: activity.name || '未命名活动',
    meta: `${formatActivityDate(activity.date)} · ${activity.place || '地点待定'}`,
    detail: `${TYPE_LABELS[mode] ?? '活动'} · 待审核`,
    tone: 'amber'
  }
}

function noticeItem(notice: CanaryNotice): StageExtraItem {
  return {
    id: `notice-${notice.id}`,
    kind: 'notice',
    title: notice.title || '未命名通知',
    meta: `${notice.author_name || '系统'} · ${formatIso(notice.created_at)}`,
    detail: clip(stripMarkdown(notice.content || ''), 70) || '无正文摘要',
    tone: 'blue'
  }
}

function buildExtra(source: StageSource, isAdmin: boolean, isDepartment: boolean): StageExtra {
  if (isAdmin) {
    return {
      title: '管理员工作台',
      subtitle: 'AI 执行与风险日志',
      items: [
        ...source.agents.slice(0, 4).map(agentItem),
        ...source.logs.slice(0, 3).map(logItem)
      ],
      emptyLabel: '暂无执行或风险记录'
    }
  }
  if (isDepartment) {
    return {
      title: '学生会工作台',
      subtitle: '待审核与 AI 执行',
      items: [
        ...source.pending.slice(0, 5).map(reviewItem),
        ...source.agents.slice(0, 2).map(agentItem)
      ],
      emptyLabel: '暂无待办事项'
    }
  }
  return {
    title: '通知与动态',
    subtitle: '站内最新通知',
    items: source.notices.slice(0, 4).map(noticeItem),
    emptyLabel: '暂无通知'
  }
}

export function buildStageData(source: StageSource): StageData {
  const roles = Array.isArray(source.profile.roles) ? source.profile.roles : []
  const isAdmin = roles.includes('admin')
  const isDepartment = roles.includes('department')
  const serverOffsetMs = source.serverOffsetMs ?? 0
  const now = new Date(Date.now() + serverOffsetMs)
  const totalHours = sumHours(source.time)

  const identity: StageIdentity = {
    name: source.profile.name || '同学',
    studentId: String(source.profile.id ?? ''),
    userOid: String(source.profile._id ?? ''),
    className: source.className || '未分配班级',
    rolesLabel: roles.map((role) => ROLE_LABELS[role] ?? role).join(' · ') || '学生',
    greeting: greetingFor(now.getHours()),
    dateLabel: dateLabelFor(now),
    totalHours,
    pendingCount: source.pending.length,
    isAdmin,
    isDepartment
  }

  const hours: StageHours = {
    total: totalHours,
    points: hourPoints(source.time)
  }

  return {
    modules: STAGE_MODULES,
    identity,
    hours,
    activities: source.activities.map(mapActivity),
    extra: buildExtra(source, isAdmin, isDepartment),
    serverOffsetMs
  }
}

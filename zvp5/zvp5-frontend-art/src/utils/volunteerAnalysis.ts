import type { UserActivityTimeSums } from '@/../types'
import type { Activity, ActivityMember } from '@/../types/v2'
import dayjs from '@/plugins/dayjs'
import { displayStatus } from './activityStatus'

/**
 * 义工详情页「AI 分析」的参考内容拼装。
 *
 * 纯函数：只把已经取到的资料渲染成 Markdown，不做请求，也不碰 i18n
 * （参考内容是给模型的提示材料，不是界面文案，与后端 SCENE_PROMPTS 一样固定中文）。
 */

/** 参考内容上限：留 500 字符余量给弹窗里的「补充片段」，不靠弹窗兜底截断。 */
export const VOLUNTEER_CONTEXT_LIMIT = 7500

/** 最近参与活动条数上限。 */
export const VOLUNTEER_ACTIVITY_LIMIT = 8

/** 数据源缺失时的占位，便于模型知道「没有查到」而不是「值为空」。 */
export const VOLUNTEER_CONTEXT_MISSING = '（未能获取）'

/** 关联义工的查询口径：系统里没有独立的关联实体，只能由模型自己去查。 */
export const VOLUNTEER_RELATION_NOTE =
  '关联义工按『共同参加活动』或『同班同学』口径，由你使用只读工具查询；不存在独立关联实体。'

/**
 * 页面传入的最小主体字段。不复用全局 `User` 是为了让失败降级时也能传
 * 「部分字段为空」的对象，而不必伪造一整个 User。
 */
export interface VolunteerSubject {
  name?: string | null
  id?: number | string | null
  _id?: string | null
  group?: string[] | null
  roles?: string[] | null
  banned?: boolean
  ban_reason?: string | null
  ban_permanent?: boolean
  ban_until?: string | null
}

/** `GET /v1/users/{id}/activities` 的行：活动本体 + 该用户的成员记录。 */
export interface VolunteerActivityRow extends Activity {
  mine?: Pick<ActivityMember, '_id' | 'member' | 'status' | 'mode' | 'duration'>
}

export interface VolunteerContextInput {
  user?: VolunteerSubject | null
  /** null / undefined = 工时接口失败。 */
  time?: UserActivityTimeSums | null
  /** null / undefined = 活动接口失败；空数组 = 确实没有记录。 */
  activities?: VolunteerActivityRow[] | null
}

const ROLE_LABELS: Record<string, string> = {
  student: '学生',
  secretary: '团支书',
  department: '学生会',
  admin: '管理员',
  auditor: '审计员',
  system: '系统'
}

const STATUS_LABELS: Record<string, string> = {
  effective: '有效',
  pending: '审核中',
  refused: '无效'
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function hours(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return VOLUNTEER_CONTEXT_MISSING
  return String(Math.round(value * 100) / 100)
}

function roleText(roles: string[] | null | undefined): string {
  const values = (roles ?? []).filter(Boolean)
  if (!values.length) return VOLUNTEER_CONTEXT_MISSING
  return values.map((role) => ROLE_LABELS[role] ?? role).join('、')
}

function banText(user: VolunteerSubject | null | undefined): string {
  if (!user) return VOLUNTEER_CONTEXT_MISSING
  if (!user.banned) return '正常'
  const reason = text(user.ban_reason)
  const head = `已封禁${reason ? `：${reason}` : '（未填写原因）'}`
  if (user.ban_permanent) return `${head}，永久`
  const until = text(user.ban_until)
  if (!until) return `${head}，未提供到期时间`
  const expiresAt = dayjs(until)
  return `${head}，至 ${expiresAt.isValid() ? expiresAt.format('YYYY-MM-DD HH:mm') : until}`
}

function escapeCell(value: string): string {
  return value.replace(/\|/g, '\\|')
}

function activityLine(row: VolunteerActivityRow): string {
  const name = escapeCell(text(row.name) || '（未命名活动）')
  const status = STATUS_LABELS[displayStatus(row)] ?? '未知'
  const duration = hours(row.mine?.duration)
  const raw = text(row.date)
  const at = raw ? dayjs(raw) : null
  const date = at?.isValid() ? at.format('YYYY-MM-DD') : raw || VOLUNTEER_CONTEXT_MISSING
  return `| ${name} | ${status} | ${duration} | ${date} |`
}

function activitySection(rows: string[], source: VolunteerActivityRow[] | null | undefined): string {
  if (!source) return VOLUNTEER_CONTEXT_MISSING
  if (!source.length) return '（暂无活动记录）'
  // 行被长度预算裁光时不能说「暂无记录」，否则模型会当成这个人没参加过活动。
  if (!rows.length) return '（活动过多，明细已按长度上限省略）'
  return ['| 活动 | 状态 | 时长（小时） | 日期 |', '| --- | --- | --- | --- |', ...rows].join('\n')
}

function compose(head: string, activity: string): string {
  return `${head}\n${activity}\n\n${VOLUNTEER_RELATION_NOTE}`
}

export function buildVolunteerContextMarkdown(input: VolunteerContextInput): string {
  const { user, time } = input
  const head = [
    '# 目标义工',
    '',
    `- 姓名：${text(user?.name) || VOLUNTEER_CONTEXT_MISSING}`,
    `- 学号：${text(user?.id) || VOLUNTEER_CONTEXT_MISSING}`,
    `- OID：${text(user?._id) || VOLUNTEER_CONTEXT_MISSING}`,
    `- 班级：${(user?.group ?? []).filter(Boolean).join('、') || VOLUNTEER_CONTEXT_MISSING}`,
    `- 角色：${roleText(user?.roles)}`,
    `- 封禁状态：${banText(user)}`,
    '',
    '## 工时（小时）',
    '',
    `- 校内：${hours(time?.['on-campus'])}`,
    `- 校外：${hours(time?.['off-campus'])}`,
    `- 社会实践：${hours(time?.['social-practice'])}`,
    '',
    `## 最近参与活动（最多 ${VOLUNTEER_ACTIVITY_LIMIT} 条）`,
    ''
  ].join('\n')

  const source = input.activities ?? null
  const allRows = (source ?? []).slice(0, VOLUNTEER_ACTIVITY_LIMIT).map(activityLine)
  let rows = allRows

  let output = compose(head, activitySection(rows, source))
  // 超长时只从尾部删活动,前面的身份/工时段落保持完整。
  while (output.length > VOLUNTEER_CONTEXT_LIMIT && rows.length) {
    rows = rows.slice(0, -1)
    output = compose(head, activitySection(rows, source))
  }
  // 兜底：身份字段本身异常长时仍不能把超限内容丢给弹窗去截。
  return output.length > VOLUNTEER_CONTEXT_LIMIT
    ? output.slice(0, VOLUNTEER_CONTEXT_LIMIT)
    : output
}

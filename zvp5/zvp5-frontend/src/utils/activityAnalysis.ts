import type { ActivityStatistics } from '@/../types'
import type { Activity } from '@/../types/v2'
import dayjs from '@/plugins/dayjs'

/**
 * 活动详情页「AI 分析」的参考内容拼装。
 *
 * 纯函数：只把已经取到的资料渲染成 Markdown，不做请求，也不碰 i18n
 * （参考内容是给模型的提示材料，与后端 SCENE_PROMPTS 一样固定中文）。
 *
 * 上下文只放四类信息：活动标识、基本信息、聚合统计、活动描述。
 * **不放成员名单** —— 名单属于个人信息，模型需要时自行调用只读工具查。
 */

/** 参考内容上限：与义工详情页一致，留 500 字符余量给弹窗里的「补充片段」。 */
export const ACTIVITY_CONTEXT_LIMIT = 7500

/** 数据源缺失时的占位，便于模型知道「没有查到」而不是「值为空」。 */
export const ACTIVITY_CONTEXT_MISSING = '（未能获取）'

/** 截断标记：截断处必须留痕，不能让模型以为上下文是完整的。 */
export const ACTIVITY_CONTEXT_TRUNCATED = '…[truncated]'

/** 班级分布最多列出的行数：班级多的时候按人数取前 N 个，其余只给一句说明。 */
export const ACTIVITY_CLASS_ROW_LIMIT = 12

export interface ActivityAnalysisInput {
  activity: Activity
  /** 报名人数；null / undefined = 没查到。 */
  membersCount?: number | null
  /** 组织人姓名；缺省只给 oid。 */
  creatorName?: string | null
  /** 聚合统计；null / undefined = 统计接口失败。 */
  statistics?: ActivityStatistics | null
}

const TYPE_LABELS: Record<string, string> = {
  'on-campus': '校内义工',
  'off-campus': '校外义工',
  'social-practice': '社会实践',
  hybrid: '特殊义工'
}

const STATUS_LABELS: Record<string, string> = {
  pending: '审核中',
  effective: '有效',
  refused: '无效'
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function typeLabel(type: Activity['type']): string {
  return TYPE_LABELS[type] ?? type
}

function statusLabel(status: Activity['status']): string {
  return STATUS_LABELS[status] ?? '未知'
}

function dateText(value: unknown): string {
  const raw = text(value)
  if (!raw) return ACTIVITY_CONTEXT_MISSING
  const at = dayjs(raw)
  return at.isValid() ? at.format('YYYY-MM-DD HH:mm') : raw
}

function hours(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ACTIVITY_CONTEXT_MISSING
  return String(Math.round(value * 100) / 100)
}

function percent(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ACTIVITY_CONTEXT_MISSING
  return `${Math.round(value * 10) / 10}%`
}

function escapeCell(value: string): string {
  return value.replace(/\|/g, '\\|')
}

/**
 * 截断到指定长度，且不把代理对劈成半个字符。
 *
 * 表情与生僻字在 UTF-16 里是两个码元，直接 slice 会留下半个代理对，
 * 序列化给后端时会变成乱码。
 */
function sliceSafe(value: string, limit: number): string {
  if (value.length <= limit) return value
  let end = Math.max(0, limit)
  const tail = value.charCodeAt(end - 1)
  const next = value.charCodeAt(end)
  if (tail >= 0xd800 && tail <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
    end -= 1
  }
  return value.slice(0, end)
}

function identitySection(activity: Activity): string {
  return [
    '# 活动标识',
    '',
    `- OID：${text(activity._id) || ACTIVITY_CONTEXT_MISSING}`,
    `- 名称：${text(activity.name) || ACTIVITY_CONTEXT_MISSING}`,
    `- 类型：${typeLabel(activity.type)}`,
    `- 状态：${statusLabel(activity.status)}`,
    ''
  ].join('\n')
}

function creatorText(activity: Activity, creatorName: string | null | undefined): string {
  const oid = text(activity.creator)
  const name = text(creatorName)
  if (name && oid) return `${name}（${oid}）`
  return name || oid || ACTIVITY_CONTEXT_MISSING
}

function basicSection(
  activity: Activity,
  creatorName: string | null | undefined,
  totalHours: number | undefined,
  meanHours: number | undefined
): string {
  return [
    '## 基本信息',
    '',
    `- 时间：${dateText(activity.date)}`,
    `- 时长：总计 ${hours(totalHours)} 小时（人均 ${hours(meanHours)} 小时）`,
    `- 地点：${text(activity.place) || ACTIVITY_CONTEXT_MISSING}`,
    `- 组织人：${creatorText(activity, creatorName)}`,
    ''
  ].join('\n')
}

function classSection(statistics: ActivityStatistics | null | undefined): string {
  const rows = (statistics?.classes ?? [])
    .filter((row) => row && (row.count > 0 || row.sum > 0))
    .slice()
    .sort((a, b) => b.count - a.count || (a.group_name || '').localeCompare(b.group_name || ''))
  if (!rows.length) {
    return '（没有班级分布数据）'
  }
  const shown = rows.slice(0, ACTIVITY_CLASS_ROW_LIMIT)
  const lines = shown.map((row) => {
    const name = escapeCell(text(row.group_name) || '（未分班）')
    return `| ${name} | ${row.count} | ${hours(row.sum)} |`
  })
  const head = ['| 班级 | 人数 | 总时长（小时） |', '| --- | --- | --- |']
  const tail =
    rows.length > shown.length
      ? ['', `（班级较多，仅列出人数最多的 ${ACTIVITY_CLASS_ROW_LIMIT} 个）`]
      : []
  return [...head, ...lines, ...tail].join('\n')
}

function statsSection(
  membersCount: number | null | undefined,
  statistics: ActivityStatistics | null | undefined
): string {
  const registered =
    typeof membersCount === 'number' && Number.isFinite(membersCount) ? membersCount : null
  const signed = statistics?.total ?? null
  const rate =
    registered && signed !== null ? (signed / registered) * 100 : undefined
  return [
    '## 聚合统计',
    '',
    `- 报名数：${registered === null ? ACTIVITY_CONTEXT_MISSING : registered}`,
    `- 签到数（有效参与）：${signed === null ? ACTIVITY_CONTEXT_MISSING : signed}`,
    `- 出勤率：${percent(rate)}`,
    `- 总时长：${hours(statistics?.description?.sum)} 小时（人均 ${hours(
      statistics?.description?.mean
    )} 小时）`,
    '',
    '### 班级分布',
    '',
    classSection(statistics),
    ''
  ].join('\n')
}

function compose(identity: string, basic: string, stats: string, description: string): string {
  const descBlock = description
    ? ['## 活动描述', '', description, ''].join('\n')
    : ['## 活动描述', '', ACTIVITY_CONTEXT_MISSING, ''].join('\n')
  return `${identity}${basic}${stats}${descBlock}`
}

export function buildActivityContextMarkdown(input: ActivityAnalysisInput): string {
  const { activity, membersCount, creatorName, statistics } = input
  const identity = identitySection(activity)
  const basic = basicSection(
    activity,
    creatorName,
    statistics?.description?.sum,
    statistics?.description?.mean
  )
  const stats = statsSection(membersCount, statistics)
  const description = text(activity.description)

  const full = compose(identity, basic, stats, description)
  if (full.length <= ACTIVITY_CONTEXT_LIMIT) {
    return full
  }

  // 超预算时先砍描述：标识、基本信息与统计是分析的骨架，不动它们。
  const budget =
    ACTIVITY_CONTEXT_LIMIT -
    (full.length - description.length) -
    ACTIVITY_CONTEXT_TRUNCATED.length -
    1
  if (budget > 0) {
    const clipped = compose(
      identity,
      basic,
      stats,
      `${sliceSafe(description, budget)}\n${ACTIVITY_CONTEXT_TRUNCATED}`
    )
    if (clipped.length <= ACTIVITY_CONTEXT_LIMIT) {
      return clipped
    }
  }

  // 兜底：连描述都砍完还超长（异常长的班级分布等），按字符硬截，绝不超预算。
  return sliceSafe(full, ACTIVITY_CONTEXT_LIMIT - ACTIVITY_CONTEXT_TRUNCATED.length - 1)
}

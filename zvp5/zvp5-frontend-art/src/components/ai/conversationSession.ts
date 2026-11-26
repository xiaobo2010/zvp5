/**
 * 会话栏的纯展示计算。
 *
 * 抽出来是为了让「相对时间怎么算」「token 怎么排版」有单测可覆盖，
 * 组件里只剩取值与拼键。
 */

/** 相对时间的形态：越近越粗，越远越具体。 */
export type RelativeTimeKind = 'justNow' | 'minutes' | 'hours' | 'days' | 'date'

export interface RelativeTime {
  kind: RelativeTimeKind
  /** 分钟 / 小时 / 天数（justNow 与 date 不需要）。 */
  count: number
  /** kind='date' 时的具体日期（YYYY-MM-DD）。 */
  date: string
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * 把服务端时间折成相对时间。
 *
 * 服务端给的是 ISO 字符串；解析不了就给 'date' + 空串（界面回落成占位符）。
 */
export function relativeTimeParts(
  value: string | null | undefined,
  now: number
): RelativeTime {
  if (!value) {
    return { kind: 'date', count: 0, date: '' }
  }
  const time = new Date(value).getTime()
  if (!Number.isFinite(time)) {
    return { kind: 'date', count: 0, date: '' }
  }
  const diff = Math.max(0, now - time)
  if (diff < MINUTE) {
    return { kind: 'justNow', count: 0, date: '' }
  }
  if (diff < HOUR) {
    return { kind: 'minutes', count: Math.floor(diff / MINUTE), date: '' }
  }
  if (diff < DAY) {
    return { kind: 'hours', count: Math.floor(diff / HOUR), date: '' }
  }
  if (diff < 7 * DAY) {
    return { kind: 'days', count: Math.floor(diff / DAY), date: '' }
  }
  const date = new Date(time)
  const fill = (n: number) => String(n).padStart(2, '0')
  return {
    kind: 'date',
    count: 0,
    date: `${date.getFullYear()}-${fill(date.getMonth() + 1)}-${fill(date.getDate())}`
  }
}

/** 千分位；负数与非法值给 0。 */
export function formatTokens(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return 0
  }
  return Math.trunc(value)
}

/**
 * 上下文占比（百分比整数）。
 *
 * 上游没报水位时给 0（不是 NaN）；超过 100% 按 100% 截断，界面上的条
 * 最多填满。
 */
export function contextPercent(ratio: number): number {
  if (!Number.isFinite(ratio) || ratio <= 0) {
    return 0
  }
  return Math.min(100, Math.round(ratio * 100))
}

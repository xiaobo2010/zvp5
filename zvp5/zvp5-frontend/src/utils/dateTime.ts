/**
 * 页面展示用的时间格式化。
 *
 * 后端时间字段可能为空、也可能带一段无法解析的字符串，这里统一口径：
 * - 空值 → 用破折号占位，绝不留空白区域；
 * - 非法日期 → 回落到原始字符串，既不显示空白，也保留排查线索。
 *
 * 该口径目前由「我的」页的通知列表使用；全局提醒弹窗（ZNotificationReminder）
 * 仍是内联实现，待其改造时改用本函数即可统一。
 */
import dayjs from '@/plugins/dayjs'

const EMPTY_PLACEHOLDER = '—'

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return EMPTY_PLACEHOLDER
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

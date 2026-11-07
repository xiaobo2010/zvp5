/**
 * 附件每日上传配额的展示单位换算。
 *
 * 后端字段 `daily_max_bytes` 的单位是**字节**；界面按 MB 输入更接近管理员的直觉
 * （没人愿意敲 104857600）。所有换算集中在这里，组件只负责取值与提交。
 */

/** 1 MB = 1024 × 1024 字节（与后端默认值 100MB = 104857600 对齐）。 */
const BYTES_PER_MB = 1024 * 1024

/** MB → 字节；空值透传 null（null 表示「继承全局」）。 */
export function mbToBytes(mb: number | null | undefined): number | null {
  if (mb === null || mb === undefined || !Number.isFinite(mb)) {
    return null
  }
  return Math.round(mb * BYTES_PER_MB)
}

/** 字节 → MB（保留一位小数）；空值透传 null。 */
export function bytesToMb(bytes: number | null | undefined): number | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) {
    return null
  }
  return Math.round((bytes / BYTES_PER_MB) * 10) / 10
}

/** 把字节格式化成 MB 文本（去掉多余的 .0），用于「当前生效」那行小字。 */
export function formatMb(bytes: number | null | undefined): string {
  const mb = bytesToMb(bytes)
  if (mb === null) {
    return '—'
  }
  return Number.isInteger(mb) ? String(mb) : mb.toFixed(1)
}

/**
 * Adaptive Card Density —— 卡片密度统一规范。
 *
 * 密度由「每页显示条数」推导：条数越多，卡片越紧凑（真实改变 padding / gap /
 * 字号 / 信息显隐），而不是 transform: scale() 整体缩放。
 *
 * 与分页 composable 配合时：
 * - 手动模式：size 固定，density = densityForSize(size)，卡片按该密度渲染。
 * - 自动模式：先按当前密度实测卡片高度 → 计算可容纳条数 → 再由条数反推密度，
 *   有限次收敛到稳定档位，避免「按卡片高度算数量、又按数量改高度」的循环。
 */

export type CardDensity = 'spacious' | 'comfortable' | 'compact' | 'ultra'

export const DENSITY_ORDER: CardDensity[] = ['spacious', 'comfortable', 'compact', 'ultra']

export const DENSITY_LABEL: Record<CardDensity, string> = {
  spacious: '舒展',
  comfortable: '标准',
  compact: '紧凑',
  ultra: '超紧凑'
}

/**
 * 按每页显示条数选择密度档位（初始参考阈值，可被自动模式的收敛校准覆盖）。
 *   1–6   → 舒展 spacious
 *   7–12  → 标准 comfortable
 *   13–24 → 紧凑 compact
 *   25+   → 超紧凑 ultra
 */
export function densityForSize(size: number): CardDensity {
  if (!Number.isFinite(size) || size <= 6) return 'spacious'
  if (size <= 12) return 'comfortable'
  if (size <= 24) return 'compact'
  return 'ultra'
}

/** 密度是否比参照档更紧凑（用于条件渲染次要信息） */
export function isDenserThan(a: CardDensity, b: CardDensity): boolean {
  return DENSITY_ORDER.indexOf(a) > DENSITY_ORDER.indexOf(b)
}

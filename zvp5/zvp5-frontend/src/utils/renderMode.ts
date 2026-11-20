/**
 * 渲染模式：精细（默认，原版视觉）/ 流畅（关闭高密度装饰）。
 *
 * 这是**设备级**偏好，存在 localStorage：同一台平板/浏览器换账号也保留，
 * 因为它描述的是「这台设备画得动多少」，不是账号资料。
 *
 * 生效方式：给 `<html>` 写 `data-render-mode`，样式层
 * `assets/styles/smooth-mode.css` 只在 `smooth` 下关闭：
 *   * 卡片边框流光（phi 主题的巡边 ::after）
 *   * 背部六边形底纹（.yg-page-hex / .z-hex-grid）
 *   * 卡片与页头里的 HUD 线条、网格、扫描线
 * 精细档不写任何覆盖，等于原版。
 *
 * 必须在 mount 之前调用 `installRenderMode()`：否则会先按精细档渲染一帧，
 * 再切到流畅档，首屏白烧一次合成/重绘。
 */

export type RenderMode = 'fine' | 'smooth'

export const RENDER_MODE_STORAGE_KEY = 'zvms.renderMode'
export const RENDER_MODE_ATTR = 'data-render-mode'

const FINE: RenderMode = 'fine'
const SMOOTH: RenderMode = 'smooth'

/** 未知/损坏的值一律回落精细，避免历史脏数据把界面锁在流畅档。 */
export function normalizeRenderMode(value: unknown): RenderMode {
  return value === SMOOTH ? SMOOTH : FINE
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    // 隐私模式/禁用存储时访问 localStorage 可能直接抛 SecurityError
    return null
  }
}

/** 读取已保存的偏好；读不到或抛错时返回精细（原版）。 */
export function readRenderMode(
  storage: Pick<Storage, 'getItem'> | null = safeStorage()
): RenderMode {
  try {
    return normalizeRenderMode(storage?.getItem(RENDER_MODE_STORAGE_KEY))
  } catch {
    return FINE
  }
}

/** 只改 DOM 属性，不落盘；返回规范化后的值。 */
export function applyRenderMode(
  mode: RenderMode,
  doc: Document = document
): RenderMode {
  const normalized = normalizeRenderMode(mode)
  doc.documentElement.setAttribute(RENDER_MODE_ATTR, normalized)
  return normalized
}

/** 用户切换：立即生效并持久化。存储失败不影响本次切换。 */
export function setRenderMode(
  mode: RenderMode,
  options: {
    doc?: Document
    storage?: Pick<Storage, 'setItem'> | null
  } = {}
): RenderMode {
  const { doc = document, storage = safeStorage() } = options
  const normalized = applyRenderMode(mode, doc)
  try {
    storage?.setItem(RENDER_MODE_STORAGE_KEY, normalized)
  } catch {
    // 配额/隐私模式：本次仍然生效，只是刷新后回到默认
  }
  return normalized
}

/** 启动时应用已保存的偏好，供 main.ts 在 mount 前调用。 */
export function installRenderMode(
  doc: Document = document,
  storage: Pick<Storage, 'getItem'> | null = safeStorage()
): RenderMode {
  return applyRenderMode(readRenderMode(storage), doc)
}

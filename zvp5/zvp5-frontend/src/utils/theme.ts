/**
 * 主题变体运行时：canary / canary2 / canary-pro 等主题在视觉层动态切换，
 * 通过 `data-theme-variant` 属性控制（与明暗 `data-theme` 正交）。
 *
 * 这是**账号级**偏好，设备级缓存 + 账号级事实：设备上保留历史选择，
 * 但登录后从服务器同步该账号的选择覆盖本地值；切换后立即落后端。
 *
 * 生效方式：给 `<html>` 写 `data-theme-variant`，样式层
 * `assets/styles/yg/theme-variants.css` 按主题加载对应令牌覆盖。
 * 默认主题 canary 不写覆盖，保留现网视觉。
 *
 * 必须在 mount 之前调用 `installTheme()`：否则会先按默认主题渲染一帧，
 * 再切到用户主题，首屏闪动。
 */

export type ThemeVariant = string

export const THEME_STORAGE_KEY = 'zvms.theme'
export const THEME_ATTR = 'data-theme-variant'

const DEFAULT_THEME: ThemeVariant = 'canary'

/**
 * 规范化主题值：非字符串或不在允许列表中一律回落到 canary。
 * allowed 可选：不传则只检查类型，传则同时检查是否在注册表中。
 */
export function normalizeTheme(value: unknown, allowed?: string[]): ThemeVariant {
  if (typeof value !== 'string' || !value) {
    return DEFAULT_THEME
  }
  if (allowed && !allowed.includes(value)) {
    return DEFAULT_THEME
  }
  return value
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    // 隐私模式/禁用存储时访问 localStorage 可能直接抛 SecurityError
    return null
  }
}

/** 读取已保存的偏好；读不到或抛错时返回默认主题 canary。 */
export function readStoredTheme(
  storage: Pick<Storage, 'getItem'> | null = safeStorage()
): ThemeVariant {
  try {
    const raw = storage?.getItem(THEME_STORAGE_KEY)
    return normalizeTheme(raw)
  } catch {
    return DEFAULT_THEME
  }
}

/** 只改 DOM 属性，不落盘；返回规范化后的值。 */
export function applyTheme(
  theme: ThemeVariant,
  doc: Document = document
): ThemeVariant {
  const normalized = normalizeTheme(theme)
  doc.documentElement.setAttribute(THEME_ATTR, normalized)
  return normalized
}

/** 用户切换：立即生效并持久化到设备缓存。存储失败不影响本次切换。 */
export function setStoredTheme(
  theme: ThemeVariant,
  storage: Pick<Storage, 'setItem'> | null = safeStorage()
): ThemeVariant {
  const normalized = applyTheme(theme)
  try {
    storage?.setItem(THEME_STORAGE_KEY, normalized)
  } catch {
    // 配额/隐私模式：本次仍然生效，只是刷新后回到默认
  }
  return normalized
}

/** 启动时应用已保存的偏好，供 main.ts 在 mount 前调用。 */
export function installTheme(
  doc: Document = document,
  storage: Pick<Storage, 'getItem'> | null = safeStorage()
): ThemeVariant {
  return applyTheme(readStoredTheme(storage), doc)
}

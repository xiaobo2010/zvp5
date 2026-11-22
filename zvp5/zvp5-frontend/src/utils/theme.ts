/**
 * 主题 = 整站多构建（same-origin multi-build），不是同一套页面换外壳。
 *
 * 每个主题都是一份**完整的前端构建**，由 `zvp5/deployment/build-theme-apps.mjs`
 * 按 `zvp5/themes/manifest.json` 在 CI 里构建后挂到同一域名下：
 * - 默认主题（canary）→ `/`
 * - 其余主题 `<id>`   → `/themes/<id>/`
 *
 * 这里**不再维护主题 id 列表**：清单是唯一事实源，主题集合在运行时由
 * `/api/v1/themes`（后端注册表）或 `/themes.json`（构建产物里的运行清单）提供。
 * 本模块只负责「id ↔ 路径」的约定与设备级缓存。
 *
 * 因为同源，localStorage 里的 token 共享，切换主题 = 整页跳转，登录态无缝。
 */

export type ThemeVariant = string

export const THEME_STORAGE_KEY = 'zvms.theme'

/** 默认主题（挂在根路径）。后端注册表与清单的 `default` 都必须是它。 */
export const DEFAULT_THEME: ThemeVariant = 'canary'

/** 非根主题的统一路径前缀；实际 URL 是 `${THEME_PREFIX}${id}/`。 */
export const THEME_PREFIX = '/themes/'

/** 合法主题 slug：小写字母/数字开头，后续小写字母/数字/连字符。 */
const THEME_ID_RE = /^[a-z0-9][a-z0-9-]*$/

/** 是否是语法合法的主题 id（不做「是否已部署」判断——那由清单/注册表决定）。 */
export function isThemeId(value: unknown): value is ThemeVariant {
  return typeof value === 'string' && THEME_ID_RE.test(value)
}

/**
 * 规范化主题值：非字符串或不是合法 slug 一律回落到 canary。
 * allowed 可选：传了就以它为准（例如后端注册表 / 运行清单给出的主题集合）。
 */
export function normalizeTheme(value: unknown, allowed?: readonly string[]): ThemeVariant {
  if (!isThemeId(value)) {
    return DEFAULT_THEME
  }
  if (allowed && !allowed.includes(value)) {
    return DEFAULT_THEME
  }
  return value
}

/** 主题 id → 站内路径；非法 id 回落到 canary 根路径。 */
export function themePath(theme: ThemeVariant, allowed?: readonly string[]): string {
  const id = normalizeTheme(theme, allowed)
  return id === DEFAULT_THEME ? '/' : `${THEME_PREFIX}${id}/`
}

/**
 * 按 pathname 反查当前主题：`/themes/<id>/...` 是 `<id>`，其它都是 canary。
 * 与清单无关，只按统一路径约定解析。
 */
export function currentThemeId(pathname: string): ThemeVariant {
  const match = /^\/themes\/([a-z0-9][a-z0-9-]*)(?:\/|$)/.exec(pathname || '/')
  return match ? match[1] : DEFAULT_THEME
}

/** 当前路径是否落在某个子应用（非根 canary）的基路径下。 */
export function isThemePath(pathname: string, theme: ThemeVariant): boolean {
  if (theme === DEFAULT_THEME) {
    return false
  }
  return currentThemeId(pathname) === normalizeTheme(theme)
}

/** 当前是否运行在根 canary 应用里（只有它能做「登录后按服务器主题跳转」）。 */
export function isRootApp(pathname: string): boolean {
  return currentThemeId(pathname) === DEFAULT_THEME
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

/** 用户切换：只写设备缓存（跳转由调用方发起）。存储失败不影响返回值。 */
export function setStoredTheme(
  theme: ThemeVariant,
  storage: Pick<Storage, 'setItem'> | null = safeStorage()
): ThemeVariant {
  const normalized = normalizeTheme(theme)
  try {
    storage?.setItem(THEME_STORAGE_KEY, normalized)
  } catch {
    // 配额/隐私模式：本次仍然返回新值，只是刷新后回到默认
  }
  return normalized
}

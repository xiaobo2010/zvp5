/**
 * 主题 = 整站多构建（same-origin multi-build），不是同一套页面换外壳。
 *
 * 三个主题各自是一份**完整的前端构建**，由 `zvp5/deployment/build-theme-apps.sh`
 * 在 CI 里从对应分支构建后挂到同一域名下：
 * - canary    → `/`（当前 `zvp5/zvp5-frontend`）
 * - canary2   → `/t/canary2/`（`v5-canary2` 的 `zvms4-modified-main/zvms4-frontend`）
 * - canary-pro→ `/t/canary-pro/`（`v5-canary-pro` 的 `zvp5/zvp5-frontend-pro`）
 *
 * 因为同源，localStorage 里的 token 共享，切换主题 = 整页跳转，登录态无缝。
 * 这里只负责「主题 id ↔ 路径」的映射和设备级缓存；主题本身由各自构建产出，
 * 前端不再按属性换皮（`data-theme-variant` / `installTheme` 已废弃）。
 */

export type ThemeVariant = string

export const THEME_STORAGE_KEY = 'zvms.theme'

/** 主题 id → 该主题完整应用的站内基路径。 */
export const THEME_PATHS: Record<string, string> = {
  canary: '/',
  canary2: '/t/canary2/',
  'canary-pro': '/t/canary-pro/'
}

export const DEFAULT_THEME: ThemeVariant = 'canary'

/** 全部已部署的主题 id（顺序即选择器顺序）。 */
export const THEME_IDS = Object.keys(THEME_PATHS)

/**
 * 规范化主题值：非字符串或不在允许列表中一律回落到 canary。
 * allowed 可选：不传则按 THEME_PATHS 校验（默认行为）。
 */
export function normalizeTheme(value: unknown, allowed?: string[]): ThemeVariant {
  if (typeof value !== 'string' || !value) {
    return DEFAULT_THEME
  }
  if (!(allowed ?? THEME_IDS).includes(value)) {
    return DEFAULT_THEME
  }
  return value
}

/** 主题 id → 站内路径；未知 id 一律回落到 canary 根路径。 */
export function themePath(theme: ThemeVariant): string {
  return THEME_PATHS[normalizeTheme(theme)] ?? THEME_PATHS[DEFAULT_THEME]
}

/**
 * 按 pathname 反查当前主题：
 * `/t/canary2/...` 是 canary2，`/t/canary-pro/...` 是 canary-pro，其它都是 canary。
 */
export function currentThemeId(pathname: string): ThemeVariant {
  const path = pathname || '/'
  for (const [id, base] of Object.entries(THEME_PATHS)) {
    if (id === DEFAULT_THEME) {
      continue
    }
    if (path === base || path.startsWith(base)) {
      return id
    }
  }
  return DEFAULT_THEME
}

/** 当前路径是否落在某个子应用（非根 canary）的基路径下。 */
export function isThemePath(pathname: string, theme: ThemeVariant): boolean {
  if (theme === DEFAULT_THEME) {
    return false
  }
  const base = themePath(theme)
  const path = pathname || '/'
  return path === base || path.startsWith(base)
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

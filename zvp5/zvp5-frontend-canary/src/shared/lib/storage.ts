/** 本地存储键集中管理，避免散落的字符串漂移。 */
export const STORAGE_KEYS = {
  token: 'zvp5.token',
  theme: 'zvp5.theme',
  locale: 'zvp5.locale',
  profile: 'zvp5.profile'
} as const

export function readToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEYS.token) ?? ''
  } catch {
    return ''
  }
}

export function writeToken(token: string): void {
  try {
    if (token) {
      localStorage.setItem(STORAGE_KEYS.token, token)
    } else {
      localStorage.removeItem(STORAGE_KEYS.token)
    }
  } catch {
    // 隐私模式下写入失败：本次会话仍然可用，刷新后需要重新登录。
  }
}

export function readJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 同上：存储不可用不影响当前会话。
  }
}

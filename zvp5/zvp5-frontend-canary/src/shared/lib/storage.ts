/** 本地存储键集中管理，避免散落的字符串漂移。 */
export const STORAGE_KEYS = {
  token: 'token',
  theme: 'zvp5.theme',
  locale: 'zvp5.locale',
  profile: 'zvp5.profile'
} as const

const LEGACY_TOKEN_KEY = 'zvp5.token'

export function readToken(): string {
  try {
    const token = localStorage.getItem(STORAGE_KEYS.token) ?? localStorage.getItem(LEGACY_TOKEN_KEY)
    if (token && localStorage.getItem(LEGACY_TOKEN_KEY) !== null) writeToken(token)
    return token ?? ''
  } catch {
    return ''
  }
}

export function writeToken(token: string): void {
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY)
    if (token) {
      localStorage.setItem(STORAGE_KEYS.token, token)
    } else {
      localStorage.removeItem(STORAGE_KEYS.token)
    }
  } catch {
    // 隐私模式下写入失败：本次会话仍然可用，刷新后需要重新登录。
  }
}

/** 只取资料查询用的 ID，会话有效性和权限由后端验证。 */
export function tokenSubject(token: string): string {
  try {
    const encoded = token.split('.')[1]
    if (!encoded) return ''
    const segment = encoded.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(segment.padEnd(Math.ceil(segment.length / 4) * 4, '=')))
    return typeof payload.sub === 'string' ? payload.sub : ''
  } catch {
    return ''
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

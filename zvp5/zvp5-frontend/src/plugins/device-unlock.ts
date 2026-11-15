import { useUserStore } from '@/stores/user'
import { getXuehaiId } from '@/plugins/ua'
import { temporaryToken } from '@/plugins/short-token'

// 学海设备（平板）访问附件媒体库需要先校验密码：后端只认 5 分钟临时凭证，
// 这里负责弹框、缓存、失效，以及给 axios 提供要注入的请求头。
// 缓存只放内存：刷新页面就该重新验证，不留可复用的长期票据。

/** 后端设备闸的固定 detail，前端据此判断「该解锁了」。 */
export const DEVICE_LOCK_DETAIL = '学海设备需要密码解锁后访问媒体库'

/** 解锁弹框的默认提示文案。 */
export const DEVICE_UNLOCK_REASON = '平板访问附件需要验证密码，解锁后 5 分钟内有效'

/** 临时凭证的默认寿命，与后端 `jwt_encode(type="temporary")` 一致。 */
const UNLOCK_TTL_MS = 5 * 60 * 1000

/** 提前作废的余量：剩 5 秒内就当作过期，避免卡着临界点发请求。 */
const EXPIRY_SKEW_MS = 5 * 1000

interface UnlockCache {
  token: string
  expiresAt: number
}

let cache: UnlockCache = { token: '', expiresAt: 0 }
// 同一个解锁窗口内的并发调用共享同一次弹框，避免连点弹出多个密码框
let pending: Promise<string> | null = null

/** 当前是不是学海设备（平板）。 */
export function deviceLocked(): boolean {
  return getXuehaiId() > 0
}

/**
 * 从 JWT 里读 `exp` 得到到期时间；解不出来就退回「签发时刻 + 5 分钟」。
 * 两者都只影响缓存寿命，真正的有效期始终由后端说了算。
 */
function expiryOf(token: string): number {
  try {
    const payload = token.split('.')[1]
    if (payload) {
      const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
      const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
      const binary = atob(padded)
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
      const claims = JSON.parse(new TextDecoder().decode(bytes)) as { exp?: unknown }
      if (typeof claims.exp === 'number' && Number.isFinite(claims.exp)) {
        return claims.exp * 1000
      }
    }
  } catch {
    // 解析失败按保守估计处理
  }
  return Date.now() + UNLOCK_TTL_MS
}

/** 仍在有效期内的缓存凭证；没有就返回空串。 */
export function cachedUnlockToken(): string {
  if (!cache.token) {
    return ''
  }
  if (Date.now() >= cache.expiresAt - EXPIRY_SKEW_MS) {
    cache = { token: '', expiresAt: 0 }
    return ''
  }
  return cache.token
}

/** 清掉缓存：后端回了 403（凭证提前失效/被吊销）时使用。 */
export function clearDeviceUnlock(): void {
  cache = { token: '', expiresAt: 0 }
}

/**
 * 确保当前设备已解锁。
 *
 * 非学海设备直接 resolve（返回空串，调用方无需分支）；有效缓存直接复用；
 * 否则弹密码框换一张 5 分钟临时凭证。用户取消会 reject，且不写缓存——
 * 调用方应当中止当前动作，不要吞掉这个 reject。
 */
export async function ensureDeviceUnlock(options?: {
  reason?: string
}): Promise<string> {
  if (!deviceLocked()) {
    return ''
  }
  const cached = cachedUnlockToken()
  if (cached) {
    return cached
  }
  if (!pending) {
    const reason = options?.reason || DEVICE_UNLOCK_REASON
    pending = temporaryToken(useUserStore()._id, reason)
      .then((token) => {
        if (!token) {
          throw new Error('设备解锁失败')
        }
        cache = { token, expiresAt: expiryOf(token) }
        return token
      })
      .finally(() => {
        pending = null
      })
  }
  return pending
}

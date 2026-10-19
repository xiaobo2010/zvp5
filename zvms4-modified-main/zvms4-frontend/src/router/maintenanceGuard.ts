import { getMaintenanceStatus } from '@/api/system/maintenance.ts'
import { useUserStore } from '@/stores/user'

// 维护状态在路由守卫里按 TTL 缓存:避免每次导航都打公开接口。
const STATUS_CACHE_TTL_MS = 30_000
// 状态接口超时不阻塞导航(fail-open),否则弱网下整个平台都进不去。
const STATUS_TIMEOUT_MS = 3_000

interface StatusCache {
  enabled: boolean
  expiresAt: number
}

let statusCache: StatusCache | null = null
let inflight: Promise<boolean | null> | null = null

export type MaintenanceGuardResult = true | { path: string; replace: true }

/** 仅用于测试/登入登出后强制刷新。 */
export function resetMaintenanceGuardCache() {
  statusCache = null
  inflight = null
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('maintenance status timeout')), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      }
    )
  })
}

/**
 * 读取维护状态。成功才写缓存;失败/超时返回 null 且不缓存,下次导航重试。
 * 同一时刻只允许一个在飞请求,避免并发导航放大请求。
 */
async function readMaintenanceEnabled(): Promise<boolean | null> {
  if (statusCache && statusCache.expiresAt > Date.now()) {
    return statusCache.enabled
  }
  if (inflight) {
    return inflight
  }
  inflight = (async () => {
    try {
      const res = await withTimeout(getMaintenanceStatus(), STATUS_TIMEOUT_MS)
      const enabled = res?.data?.data?.enabled === true
      statusCache = { enabled, expiresAt: Date.now() + STATUS_CACHE_TTL_MS }
      return enabled
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()
  return inflight
}

function isLoggedIn(): boolean {
  try {
    return useUserStore().isLogin || Boolean(localStorage.getItem('token'))
  } catch {
    return Boolean(localStorage.getItem('token'))
  }
}

function isAdmin(): boolean {
  try {
    const position = useUserStore().position
    return Array.isArray(position) && position.includes('admin')
  } catch {
    // store 尚未初始化(pinia 未挂载)时按普通用户处理,不放行
    return false
  }
}

/**
 * 维护模式路由守卫:
 * - 维护中:除 admin 外全部收敛到 /maintenance(已在维护页则放行,避免自跳循环)
 * - 非维护中:访问 /maintenance 时按登录态送回 /user 或 /user/login
 * - 状态接口失败/超时:放行且不缓存失败结果
 */
export async function maintenanceGuard(
  to: Pick<{ path: string }, 'path'>
): Promise<MaintenanceGuardResult> {
  const enabled = await readMaintenanceEnabled()

  if (enabled === null) {
    return true
  }

  if (enabled) {
    if (to.path === '/maintenance' || isAdmin()) {
      return true
    }
    return { path: '/maintenance', replace: true }
  }

  if (to.path === '/maintenance') {
    return { path: isLoggedIn() ? '/user' : '/user/login', replace: true }
  }

  return true
}

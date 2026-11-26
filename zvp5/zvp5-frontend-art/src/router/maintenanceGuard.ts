import { getMaintenanceStatus } from '@/api/system/maintenance.ts'
import { useUserStore } from '@/stores/user'

// 维护状态在路由守卫里按 TTL 缓存:避免每次导航都打公开接口。
const STATUS_CACHE_TTL_MS = 30_000
// 状态接口超时不阻塞导航(fail-open),否则弱网下整个平台都进不去。
const STATUS_TIMEOUT_MS = 3_000

interface StatusCache {
  enabled: boolean
  admin: boolean
  expiresAt: number
}

export interface MaintenanceState {
  enabled: boolean
  admin: boolean
}

let statusCache: StatusCache | null = null
let inflight: Promise<MaintenanceState | null> | null = null

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
 * admin 与服务端同一次响应返回,不额外发请求。
 */
async function readMaintenanceState(): Promise<MaintenanceState | null> {
  if (statusCache && statusCache.expiresAt > Date.now()) {
    return { enabled: statusCache.enabled, admin: statusCache.admin }
  }
  if (inflight) {
    return inflight
  }
  inflight = (async () => {
    try {
      const res = await withTimeout(getMaintenanceStatus(), STATUS_TIMEOUT_MS)
      const data = res?.data?.data
      const enabled = data?.enabled === true
      const admin = data?.admin === true
      statusCache = { enabled, admin, expiresAt: Date.now() + STATUS_CACHE_TTL_MS }
      return { enabled, admin }
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

function isAdmin(serverAdmin: boolean): boolean {
  // 服务端用 token 复核出的 admin 最可信:position 由小组权限推导,登录未就绪
  // 时可能为空,只看它会把真管理员在维护期间弹回维护页。
  if (serverAdmin) {
    return true
  }
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
 * - 维护中:/maintenance 与 /user/login 永远放行(登录页是管理员唯一入口);
 *   其余路径仅 admin 放行,否则收敛到 /maintenance
 * - 非维护中:访问 /maintenance 时按登录态送回 /user 或 /user/login
 * - 状态接口失败/超时:放行且不缓存失败结果
 */
export async function maintenanceGuard(
  to: Pick<{ path: string }, 'path'>
): Promise<MaintenanceGuardResult> {
  const state = await readMaintenanceState()

  if (state === null) {
    return true
  }

  if (state.enabled) {
    if (
      to.path === '/maintenance' ||
      to.path === '/user/login' ||
      isAdmin(state.admin)
    ) {
      return true
    }
    return { path: '/maintenance', replace: true }
  }

  if (to.path === '/maintenance') {
    return { path: isLoggedIn() ? '/user' : '/user/login', replace: true }
  }

  return true
}

import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useGroupsStore } from '@/stores/groups'
import { prefetchRoute, warmupFonts } from './routePrefetch'

/**
 * 登录揭示动画期间的首页预热。
 *
 * 目标不是“等动画结束再请求”，而是把首页首屏真正要用的数据在幕布后面
 * 并行拿完：路由 chunk、显示字体、时长汇总、群组标签。首页挂载时直接命中
 * store / axios 缓存，揭幕即完成，不再让 P620 用户盯着骨架等接口。
 *
 * 只预热首页必需项；管理端工作台的 pending/AI/logs 请求较重，仍交给首页
 * 自己的空闲/可见时机，不和揭示动画抢 CPU 与带宽。
 */
let prefetchedKey = ''

export function prefetchLoginHome(router: Router): void {
  const user = useUserStore()
  if (!user.isLogin) return
  const key = String(user._id || user.id || '')
  if (!key || prefetchedKey === key) return
  prefetchedKey = key

  // 路由 chunk 与字体是首屏“可见性”依赖，必须立即发，不能排到
  // requestIdleCallback：揭示期间主线程忙，idle 很可能被推到 2s 以后。
  prefetchRoute(router, '/user')
  warmupFonts(['400 1em Bebas', '500 1em Rajdhani', '700 1em Rajdhani'])

  const groups = useGroupsStore()
  const tasks: Array<Promise<unknown>> = user.groups.map((group) => groups.fetchGroup(group))
  // 时长接口按 OID 读；老数据只有数字 id 时不要拼出 /v1/users//time。
  if (user._id) {
    tasks.unshift(user.getUserActivityTime())
  }
  void Promise.allSettled(tasks)
}

export function resetLoginHomePrefetch(): void {
  prefetchedKey = ''
}

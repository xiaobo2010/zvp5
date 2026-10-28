import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'

// 义工登记的两个页面（操作选择页 + 编辑器）共用同一套准入判断：
// 必须已登录，且职位里带 admin / department。与改造前的登记页口径完全一致。
// 单独成模块是为了让入口页和编辑器不会各自漂移出一条不同的权限规则。

export function hasRegisterRole(position: readonly string[]): boolean {
  return position.includes('admin') || position.includes('department')
}

// 职位是登录后异步补全的：首帧 position 为空时先刷新一次再下结论，
// 刷新失败按无权限处理（与旧页面的 catch 语义相同）。
// 通过返回 true；未登录去登录页、已登录但无权限去 404，保持旧页面的两种跳转。
export async function ensureRegisterAccess(router: Router): Promise<boolean> {
  const user = useUserStore()
  if (!user.isLogin) {
    await router.replace('/user/login')
    return false
  }
  if (!hasRegisterRole(user.position)) {
    await user.refreshUser().catch(() => undefined)
  }
  if (!hasRegisterRole(user.position)) {
    await router.replace('/not-found')
    return false
  }
  return true
}

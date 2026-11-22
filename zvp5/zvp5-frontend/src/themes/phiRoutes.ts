/**
 * Phi 设计的路由判定。
 *
 * `.yg-phi` 类、body 级弹层令牌（`body.yg-phi-portals`）与蜂窝/速度线都依赖
 * 同一张路由表；集中在这里，避免 App 外壳与 CanaryShell 各维护一份后漂移。
 */
export function isPhiPath(path: string): boolean {
  return (
    path === '/user' ||
    path.startsWith('/user/') ||
    path.startsWith('/activities') ||
    path.startsWith('/activity/') ||
    path === '/me' ||
    path === '/about' ||
    path === '/ai' ||
    path.startsWith('/ai/') ||
    path === '/server' ||
    path.startsWith('/server/') ||
    path === '/manage' ||
    path.startsWith('/manage/') ||
    path.startsWith('/group/')
  )
}

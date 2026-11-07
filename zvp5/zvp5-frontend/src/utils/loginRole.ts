export type LoginRoleKey = 'admin' | 'secretary' | 'department' | 'student'

/**
 * 登录揭示动画只展示四类身份（管理员 / 团支书 / 学生会 / 学生）。
 * 同一账号可能带多个权限，按展示顺序取最靠前的一个：
 * 管理员 > 团支书 > 学生会 > 学生。
 */
export function loginRoleKey(positions: readonly string[] | null | undefined): LoginRoleKey {
  if (!positions || positions.length === 0) {
    return 'student'
  }
  if (positions.includes('admin') || positions.includes('system')) {
    return 'admin'
  }
  if (positions.includes('secretary')) {
    return 'secretary'
  }
  if (positions.includes('department')) {
    return 'department'
  }
  return 'student'
}

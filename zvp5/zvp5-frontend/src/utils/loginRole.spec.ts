import { describe, expect, it } from 'vitest'
import { loginRoleKey } from './loginRole'

describe('loginRoleKey', () => {
  it('空身份按学生处理', () => {
    expect(loginRoleKey([])).toBe('student')
    expect(loginRoleKey(undefined)).toBe('student')
    expect(loginRoleKey(null)).toBe('student')
  })

  it('管理员优先于其他身份', () => {
    expect(loginRoleKey(['department', 'admin'])).toBe('admin')
    expect(loginRoleKey(['system'])).toBe('admin')
  })

  it('团支书优先于学生会', () => {
    expect(loginRoleKey(['department', 'secretary'])).toBe('secretary')
  })

  it('只有学生时仍然是学生', () => {
    expect(loginRoleKey(['student'])).toBe('student')
  })
})

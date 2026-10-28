import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { ensureRegisterAccess, hasRegisterRole } from './registerAccess'

// 登记入口页与编辑器共用同一个准入 helper，这里锁住三条分支：
// 未登录 → 登录页；无权限（刷新后仍无）→ 404；admin/department → 放行。
const { storeState, replaceSpy } = vi.hoisted(() => ({
  storeState: {
    isLogin: true,
    position: [] as string[],
    refreshUser: vi.fn()
  },
  replaceSpy: vi.fn()
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => storeState
}))

const router = { replace: replaceSpy } as unknown as Router

beforeEach(() => {
  storeState.isLogin = true
  storeState.position = []
  storeState.refreshUser = vi.fn(async () => {})
  replaceSpy.mockReset()
  replaceSpy.mockResolvedValue(undefined)
})

describe('hasRegisterRole', () => {
  it('accepts admin and department only', () => {
    expect(hasRegisterRole(['admin'])).toBe(true)
    expect(hasRegisterRole(['department'])).toBe(true)
    expect(hasRegisterRole(['secretary'])).toBe(false)
    expect(hasRegisterRole([])).toBe(false)
  })
})

describe('ensureRegisterAccess', () => {
  it('sends anonymous visitors to the login page', async () => {
    storeState.isLogin = false

    await expect(ensureRegisterAccess(router)).resolves.toBe(false)
    expect(replaceSpy).toHaveBeenCalledWith('/user/login')
    expect(storeState.refreshUser).not.toHaveBeenCalled()
  })

  it('lets admins and department members through without refreshing', async () => {
    storeState.position = ['department']

    await expect(ensureRegisterAccess(router)).resolves.toBe(true)
    expect(replaceSpy).not.toHaveBeenCalled()
    expect(storeState.refreshUser).not.toHaveBeenCalled()
  })

  it('refreshes an empty position once before deciding', async () => {
    storeState.position = []
    storeState.refreshUser = vi.fn(async () => {
      storeState.position = ['admin']
    })

    await expect(ensureRegisterAccess(router)).resolves.toBe(true)
    expect(storeState.refreshUser).toHaveBeenCalledTimes(1)
    expect(replaceSpy).not.toHaveBeenCalled()
  })

  it('sends users who stay unauthorised to the 404 page', async () => {
    storeState.position = ['student']

    await expect(ensureRegisterAccess(router)).resolves.toBe(false)
    expect(replaceSpy).toHaveBeenCalledWith('/not-found')
  })

  it('treats a failed refresh as unauthorised', async () => {
    storeState.position = []
    storeState.refreshUser = vi.fn(async () => {
      throw new Error('offline')
    })

    await expect(ensureRegisterAccess(router)).resolves.toBe(false)
    expect(replaceSpy).toHaveBeenCalledWith('/not-found')
  })
})

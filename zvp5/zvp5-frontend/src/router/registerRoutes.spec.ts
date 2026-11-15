import { describe, expect, it, vi } from 'vitest'

// 只解析路由表，不真正导航：懒加载的页面组件不会因此被 import。
// maintenanceGuard 的请求在这里无关紧要，统一 mock 掉避免测试触网。
vi.mock('@/api/system/maintenance.ts', () => ({
  getMaintenanceStatus: vi.fn()
}))
vi.mock('@/api', () => ({ default: {} }))

import router, { registerEntryTarget, resolveRouterBase } from './index'

describe('register routes', () => {
  it('keeps /activity/register as the operation chooser', () => {
    const resolved = router.resolve('/activity/register')

    expect(resolved.name).toBe('activity-register')
  })

  it.each(['create', 'edit'] as const)('resolves /activity/register/%s to the editor', (mode) => {
    const resolved = router.resolve(`/activity/register/${mode}?activity=act-1`)

    expect(resolved.name).toBe('activity-register-mode')
    expect(resolved.params.mode).toBe(mode)
    expect(resolved.query).toEqual({ activity: 'act-1' })
  })

  it('does not treat an unknown mode as an editor route', () => {
    expect(router.resolve('/activity/register/unknown').name).toBe('not-found')
  })
})

describe('registerEntryTarget', () => {
  it('deep-links an activity straight to the create mode and keeps every query param', () => {
    const target = registerEntryTarget({ activity: 'act-1', session: 'sess-1' })

    expect(target).toEqual({
      name: 'activity-register-mode',
      params: { mode: 'create' },
      query: { activity: 'act-1', session: 'sess-1' },
      replace: true
    })
  })

  it('leaves plain /activity/register on the chooser', () => {
    expect(registerEntryTarget({})).toBeNull()
  })

  it('ignores blank activity values', () => {
    expect(registerEntryTarget({ activity: '   ' })).toBeNull()
    expect(registerEntryTarget({ activity: null })).toBeNull()
    expect(registerEntryTarget({ activity: ['', '  '] })).toBeNull()
  })

  it('takes the first usable value from a repeated activity query', () => {
    expect(registerEntryTarget({ activity: ['', 'act-2'] })).toMatchObject({
      params: { mode: 'create' },
      query: { activity: ['', 'act-2'] }
    })
  })
})
describe('resolveRouterBase', () => {
  it.each([
    [undefined, '/'],
    ['/', '/'],
    ['https://zvp.xiaobopisces.top/', '/'],
    ['https://zvp.xiaobopisces.top/app', '/app/'],
    ['https://zvp.xiaobopisces.top/app/', '/app/']
  ])('maps page base %s to router base %s', (pageBase, expected) => {
    expect(resolveRouterBase(pageBase)).toBe(expected)
  })
})

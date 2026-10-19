import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getMaintenanceStatus } from '@/api/system/maintenance.ts'
import { useUserStore } from '@/stores/user'
import { maintenanceGuard, resetMaintenanceGuardCache } from './maintenanceGuard'

vi.mock('@/api/system/maintenance.ts', () => ({
  getMaintenanceStatus: vi.fn()
}))
vi.mock('@/api', () => ({ default: {} }))

const statusMock = vi.mocked(getMaintenanceStatus)

type StatusResult = Awaited<ReturnType<typeof getMaintenanceStatus>>

function mockStatus(enabled: boolean) {
  statusMock.mockResolvedValue({
    data: { status: 'success', code: 0, data: { enabled, since: null, announcement: null } }
  } as unknown as StatusResult)
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  resetMaintenanceGuardCache()
  statusMock.mockReset()
})

it('redirects a normal user to /maintenance while maintenance is enabled', async () => {
  mockStatus(true)
  useUserStore().position = ['student']

  await expect(maintenanceGuard({ path: '/user' })).resolves.toEqual({
    path: '/maintenance',
    replace: true
  })
})

it('lets an admin through while maintenance is enabled', async () => {
  mockStatus(true)
  useUserStore().position = ['admin']

  await expect(maintenanceGuard({ path: '/user' })).resolves.toBe(true)
})

it('allows /maintenance itself while enabled and reuses the cached status', async () => {
  mockStatus(true)

  await expect(maintenanceGuard({ path: '/maintenance' })).resolves.toBe(true)
  // 同一 TTL 内的第二次导航不应再请求接口
  await expect(maintenanceGuard({ path: '/user' })).resolves.toEqual({
    path: '/maintenance',
    replace: true
  })
  expect(statusMock).toHaveBeenCalledTimes(1)
})

it('sends a logged-out visitor from /maintenance to /user/login when not maintaining', async () => {
  mockStatus(false)
  const store = useUserStore()
  store.isLogin = false

  await expect(maintenanceGuard({ path: '/maintenance' })).resolves.toEqual({
    path: '/user/login',
    replace: true
  })
})

it('sends a logged-in user from /maintenance to /user when not maintaining', async () => {
  mockStatus(false)
  const store = useUserStore()
  store.isLogin = true

  await expect(maintenanceGuard({ path: '/maintenance' })).resolves.toEqual({
    path: '/user',
    replace: true
  })
})

it('fails open on status errors without caching the failure', async () => {
  statusMock.mockRejectedValueOnce(new Error('network down'))

  await expect(maintenanceGuard({ path: '/user' })).resolves.toBe(true)
  expect(statusMock).toHaveBeenCalledTimes(1)

  // 失败结果未缓存:下一次导航会重试,并拿到真实状态
  mockStatus(true)
  await expect(maintenanceGuard({ path: '/user' })).resolves.toEqual({
    path: '/maintenance',
    replace: true
  })
  expect(statusMock).toHaveBeenCalledTimes(2)
})

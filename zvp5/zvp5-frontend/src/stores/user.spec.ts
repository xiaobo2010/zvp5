import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import api from '@/api'
import { clearCache } from '@/utils/idbCache'
import { useUserStore } from './user'

vi.mock('@/api', () => ({
  default: {
    user: {
      auth: { useLongTermAuth: vi.fn(), logout: vi.fn().mockResolvedValue(undefined) }
    }
  }
}))
vi.mock('@/utils/idbCache', () => ({ clearCache: vi.fn().mockResolvedValue(undefined) }))

beforeEach(() => { setActivePinia(createPinia()); localStorage.clear() })

it('uses the server weak-password flag for TOTP logins', async () => {
  vi.mocked(api.user.auth.useLongTermAuth).mockResolvedValue({
    token: 'session', _id: 'user', weak_password: false,
    profile: { _id: 'user', id: '123', name: 'Name', roles: ['student'], class_id: '' }
  })
  const store = useUserStore()
  await store.setUser('123', '*123456*')
  expect(store.isLogin).toBe(true)
  expect(store.shouldResetPassword).toBe(false)
})

it('clears identity and hours immediately while cache cleanup is pending', async () => {
  let finish!: () => void
  vi.mocked(clearCache).mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve }))
  const store = useUserStore()
  store._id = 'old'
  store.isLogin = true
  store.time.onCampus = 42
  localStorage.setItem('token', 'old-token')
  const logout = store.removeUser()
  expect(store._id).toBe('')
  expect(store.isLogin).toBe(false)
  expect(store.time.onCampus).toBe(0)
  expect(localStorage.getItem('token')).toBeNull()
  finish()
  await logout
})

it('revokes the server session and still clears locally when it fails', async () => {
  vi.mocked(api.user.auth.logout).mockRejectedValueOnce(new Error('network down'))
  const store = useUserStore()
  store._id = 'old'
  store.isLogin = true
  localStorage.setItem('token', 'old-token')

  await store.removeUser()

  expect(api.user.auth.logout).toHaveBeenCalled()
  expect(localStorage.getItem('token')).toBeNull()
  expect(store.isLogin).toBe(false)
})

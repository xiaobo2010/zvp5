import { beforeEach, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosResponse } from 'axios'
import client from './axios'
import { cacheGet, cacheSet } from '@/utils/idbCache'

vi.mock('@/router', () => ({ default: { currentRoute: { value: { path: '/' } }, push: vi.fn(), replace: vi.fn() } }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ removeUser: vi.fn().mockResolvedValue(undefined) }) }))
vi.mock('@/utils/idbCache', () => ({ cacheGet: vi.fn().mockResolvedValue(null), cacheSet: vi.fn() }))
vi.mock('@/plugins/ua', () => ({ getXuehaiId: () => 0 }))
vi.mock('element-plus', () => ({ ElMessage: vi.fn() }))
vi.mock('nprogress', () => ({ default: { start: vi.fn(), done: vi.fn() } }))

beforeEach(() => { vi.clearAllMocks(); localStorage.clear() })

it('never caches authenticated GET responses', async () => {
  localStorage.setItem('token', 'alice')
  client.defaults.adapter = async (config) => ({ data: { private: true }, status: 200, statusText: 'OK', headers: {}, config })
  await client.get('/users/alice')
  expect(cacheSet).not.toHaveBeenCalled()
})

it('never falls back to another identity cache when authenticated GET fails', async () => {
  localStorage.setItem('token', 'bob')
  client.defaults.adapter = async (config) => { throw new AxiosError('offline', 'ERR_NETWORK', config) }
  await expect(client.get('/users/bob')).rejects.toThrow('offline')
  expect(cacheGet).not.toHaveBeenCalled()
})

it('preserves error rejection for unauthorized responses', async () => {
  client.defaults.adapter = async (config) => {
    const response: AxiosResponse = { data: { detail: 'expired' }, status: 401, statusText: 'Unauthorized', headers: {}, config }
    throw new AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined, response)
  }
  await expect(client.get('/users/me')).rejects.toThrow('expired')
})

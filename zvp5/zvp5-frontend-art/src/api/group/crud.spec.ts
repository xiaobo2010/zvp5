import { expect, it, vi } from 'vitest'
import axios from '@/plugins/axios'
import { getGroupsV2 } from './crud'

vi.mock('@/plugins/axios', () => ({
  default: vi.fn()
}))

vi.mock('@/plugins/short-token', () => ({
  temporaryToken: vi.fn()
}))

vi.mock('element-plus', () => ({
  ElNotification: vi.fn()
}))

it('clamps oversized V2 group pages before sending the request', async () => {
  vi.mocked(axios).mockResolvedValue({
    data: { data: [], total: 0 }
  } as never)

  await getGroupsV2(1, 1000, '')

  expect(axios).toHaveBeenCalledWith('/v1/groups', {
    params: { page: 1, size: 100, query: '' }
  })
})

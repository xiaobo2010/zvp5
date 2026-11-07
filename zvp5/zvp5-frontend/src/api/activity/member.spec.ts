import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from '@/plugins/axios'
import { insertMany } from './member'

vi.mock('@/plugins/axios', () => ({ default: vi.fn() }))
vi.mock('@/plugins/short-token', () => ({ temporaryToken: vi.fn() }))

const request = vi.mocked(axios)
const AID = 'a'.repeat(24)
const MEMBER = 'b'.repeat(24)

beforeEach(() => {
  request.mockReset()
})

describe('批量新增活动成员', () => {
  it('默认不带任何覆盖参数，保持既有的累加语义', async () => {
    await insertMany(AID, [{ member: MEMBER, mode: 'on-campus', duration: 2 }])

    expect(request).toHaveBeenCalledWith(
      `/v1/activities/${AID}/members`,
      expect.objectContaining({ method: 'post', params: undefined })
    )
  })

  it('rejectExisting 让后端在命中已有记录时拒绝整批写入', async () => {
    await insertMany(AID, [{ member: MEMBER, mode: 'on-campus', duration: 2 }], {
      rejectExisting: true
    })

    expect(request).toHaveBeenCalledWith(
      `/v1/activities/${AID}/members`,
      expect.objectContaining({
        params: { overwrite: false, reject_existing: true }
      })
    )
  })

  it('overwrite 与 rejectExisting 都显式落到查询参数', async () => {
    await insertMany(AID, [{ member: MEMBER, mode: 'on-campus', duration: 2 }], {
      overwrite: true,
      rejectExisting: true
    })

    expect(request).toHaveBeenCalledWith(
      `/v1/activities/${AID}/members`,
      expect.objectContaining({
        params: { overwrite: true, reject_existing: true }
      })
    )
  })
})

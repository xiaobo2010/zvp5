import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AI_LOGS_PERPAGE_MAX, listAiLogs, toEpochSeconds } from './logs'
import { AiRequestError } from './errors'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

const rowFixture = {
  oid: '69b7a4ad0243d973f1e1a2c1',
  user_oid: '69b7a4ad0243d973f1e1a2b2',
  user_name: '张三',
  capability: 'ocr' as const,
  endpoint: '/ai/ocr',
  provider: 'baidu',
  model: 'ocr-general',
  status: 'success' as const,
  http_status: 200,
  error_code: null,
  latency_ms: 812,
  units: 1,
  input_bytes: 20480,
  request_id: 'req-0001',
  key_scope: 'shared' as const,
  created_at: '2026-10-24T08:00:00'
}

function envelope(data: unknown, size: number) {
  return {
    status: 200,
    data: {
      status: 'ok',
      code: 200,
      data,
      metadata: { size }
    }
  }
}

beforeEach(() => {
  axiosMock.mockReset()
})

describe('时间边界折算', () => {
  it('Date 折成 epoch 秒', () => {
    expect(toEpochSeconds(new Date('2026-10-24T08:00:00Z'))).toBe(1792828800)
  })

  it('ISO 字符串折成 epoch 秒', () => {
    expect(toEpochSeconds('2026-10-24T08:00:00Z')).toBe(1792828800)
  })

  it('已经是 epoch 秒的数字原样保留', () => {
    expect(toEpochSeconds(1792828800)).toBe(1792828800)
  })

  it('空值与坏值返回 undefined（该过滤项不生效）', () => {
    expect(toEpochSeconds(null)).toBeUndefined()
    expect(toEpochSeconds(undefined)).toBeUndefined()
    expect(toEpochSeconds('')).toBeUndefined()
    expect(toEpochSeconds('not-a-date')).toBeUndefined()
    expect(toEpochSeconds(Number.NaN)).toBeUndefined()
  })
})

describe('调用日志 API 契约', () => {
  it('默认带上第一页与每页 20 条', async () => {
    axiosMock.mockResolvedValueOnce(envelope([rowFixture], 1))

    await listAiLogs()

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: '/ai/logs',
        params: { page: 1, perpage: 20 }
      })
    )
  })

  it('空过滤条件不落到 query string', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAiLogs({
      page: 3,
      perpage: 50,
      user_oid: '   ',
      capability: undefined,
      status: undefined,
      key_scope: undefined
    })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        params: { page: 3, perpage: 50 }
      })
    )
  })

  it('过滤条件逐字传给后端', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAiLogs({
      user_oid: 'uid-1',
      capability: 'chat',
      status: 'timeout',
      key_scope: 'server',
      time_start: new Date('2026-10-01T00:00:00Z'),
      time_end: new Date('2026-10-24T00:00:00Z')
    })

    const params = axiosMock.mock.calls[0]![0].params as Record<string, unknown>
    expect(params.user_oid).toBe('uid-1')
    expect(params.capability).toBe('chat')
    expect(params.status).toBe('timeout')
    expect(params.key_scope).toBe('server')
    expect(params.time_start).toBe(Math.floor(new Date('2026-10-01T00:00:00Z').getTime() / 1000))
    expect(params.time_end).toBe(Math.floor(new Date('2026-10-24T00:00:00Z').getTime() / 1000))
  })

  it('每页条数被夹到上限以内', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAiLogs({ perpage: 5000 })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ params: { page: 1, perpage: AI_LOGS_PERPAGE_MAX } })
    )
  })

  it('metadata.size 作为总数读出', async () => {
    axiosMock.mockResolvedValueOnce(envelope([rowFixture], 137))

    const result = await listAiLogs()

    expect(result.total).toBe(137)
    expect(result.rows).toEqual([rowFixture])
  })

  it('错误响应抛出带错误码的 AI 错误', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 400,
      data: { detail: 'AI_LOGS_BAD_FILTER' },
      headers: {}
    })

    await expect(listAiLogs({ status: 'nope' as never })).rejects.toMatchObject({
      name: 'AiRequestError',
      status: 400,
      code: 'AI_LOGS_BAD_FILTER'
    })
  })

  it('错误对象是 AiRequestError 实例', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 403,
      data: { detail: 'Permission denied' },
      headers: {}
    })

    const error = await listAiLogs().catch((err: unknown) => err)

    expect(error).toBeInstanceOf(AiRequestError)
  })
})

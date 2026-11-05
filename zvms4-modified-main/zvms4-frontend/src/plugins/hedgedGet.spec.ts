import { describe, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import { createHedgedGetAdapter, hedgedGetRelayBase, SMALL_GET_RACE_LIMIT_BYTES } from './hedgedGet'

function config(overrides: Record<string, unknown> = {}) {
  return {
    baseURL: 'https://api-seoul.example.test/api',
    url: '/version',
    method: 'get',
    headers: {},
    ...overrides
  } as never
}

function response(data: unknown, status = 200): AxiosResponse {
  return {
    data,
    status,
    statusText: 'OK',
    headers: {},
    config: config()
  }
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

describe('小型 GET 双路竞速', () => {
  it('只为普通 GET 选择页面源备用线路', () => {
    const primary = 'https://api-seoul.example.test/api'
    const relay = 'https://content-la.example.test/api'

    expect(hedgedGetRelayBase(config(), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ method: 'post' }), primary, relay)).toBeNull()
    expect(hedgedGetRelayBase(config({ responseType: 'blob' }), primary, relay)).toBeNull()
    expect(
      hedgedGetRelayBase(config({ headers: { Range: 'bytes=0-' } }), primary, relay)
    ).toBeNull()
    expect(hedgedGetRelayBase(config(), primary, primary)).toBeNull()
  })

  it('完整响应先到者获胜，并中止另一路', async () => {
    let primaryAborted = false
    let relayAborted = false
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      const wait = isRelay ? 5 : 60
      await delay(wait)
      if (request.signal?.aborted) {
        if (isRelay) relayAborted = true
        else primaryAborted = true
        throw new AxiosError('aborted', AxiosError.ERR_CANCELED, request)
      }
      return response(isRelay ? { source: 'relay' } : { source: 'primary' })
    }

    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api'
    )(config())
    expect(result.data).toEqual({ source: 'relay' })
    await delay(80)
    expect(primaryAborted).toBe(true)
    expect(relayAborted).toBe(false)
  })

  it('后端失败时仍由 前端 备用线路完成', async () => {
    const calls: string[] = []
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      calls.push(isRelay ? 'relay' : 'primary')
      if (!isRelay) {
        throw new AxiosError('offline', AxiosError.ERR_NETWORK, request)
      }
      return response({ ok: true })
    }

    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api'
    )(config())
    expect(result.data).toEqual({ ok: true })
    expect(calls.sort()).toEqual(['primary', 'relay'])
  })

  it('备用线路超过小响应上限时不接管大响应', async () => {
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      return response('x'.repeat(isRelay ? SMALL_GET_RACE_LIMIT_BYTES + 1 : 4))
    }

    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api'
    )(config())
    expect(result.data).toBe('xxxx')
  })

  it('两路都失败时保留后端主线路错误', async () => {
    const primary = new AxiosError('primary failed', AxiosError.ERR_NETWORK)
    const relay = new AxiosError('relay failed', AxiosError.ERR_NETWORK)
    const adapter = vi.fn<AxiosAdapter>(async (request) => {
      throw request.baseURL === 'https://content-la.example.test/api' ? relay : primary
    })

    await expect(
      createHedgedGetAdapter(adapter, 'https://content-la.example.test/api')(config())
    ).rejects.toBe(primary)
  })
})

import { describe, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import { createHedgedGetAdapter, hedgedGetRelayBase, isSmallJsonGetPath } from './hedgedGet'

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
  it('只竞速白名单内的普通 JSON GET', () => {
    const primary = 'https://api-seoul.example.test/api'
    const relay = 'https://content-la.example.test/api'

    expect(hedgedGetRelayBase(config(), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ url: '/v3/notifications' }), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ method: 'post' }), primary, relay)).toBeNull()
    expect(hedgedGetRelayBase(config({ responseType: 'blob' }), primary, relay)).toBeNull()
    expect(hedgedGetRelayBase(config({ url: '/v2/users/me/time' }), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ url: '/v3/users/me/device' }), primary, relay)).toBe(relay)
    expect(
      hedgedGetRelayBase(config({ headers: { Range: 'bytes=0-' } }), primary, relay)
    ).toBeNull()
    expect(hedgedGetRelayBase(config(), primary, primary)).toBeNull()
    expect(hedgedGetRelayBase(config(), primary, '')).toBeNull()
  })

  it('白名单覆盖常用首页、通知、活动和详情接口', () => {
    for (const path of [
      '/version',
      '/health',
      '/changelog',
      '/cert',
      '/meta',
      '/session',
      '/logs',
      '/system/maintenance',
      '/system/role-policies',
      '/system/role-policies/me',
      '/v3/notifications',
      '/v2/activities',
      '/v3/activities',
      '/v2/activities/abc',
      '/v2/activities/abc/members',
      '/v2/activities/abc/members/member',
      '/v3/activities/abc/members',
      '/v2/statistics/activities/abc/analysis',
      '/v2/users',
      '/v2/users/abc/time',
      '/v2/users/abc/time_statistics',
      '/v2/users/abc/activities',
      '/v2/groups',
      '/v2/groups/abc/activities',
      '/v2/groups/abc/users',
      '/v3/groups/abc/users',
      '/v3/users/me/device',
      '/v3/users/me/device-bindings',
      '/users/abc',
      '/v3/users/abc',
      '/v3/ban/rules',
      '/v3/ban/triggers',
      '/v3/ban/users',
      '/v3/ban/devices',
      '/v3/ban/violations',
      '/attachments',
      '/attachments/abc/meta',
      '/attachments/abc/audit',
      '/attachments/abc/ocr',
      '/ai/conversations',
      '/ai/conversations/abc',
      '/ai/conversations/abc/messages',
      '/ai/runs',
      '/ai/runs/abc',
      '/ai/agent/proposals',
      '/ai/models',
      '/ai/v1/models',
      '/ai/logs',
      '/ai/logs/stats',
      '/ai/quota/usage',
      '/ai/quota/abc',
      '/ai/scheduled-tasks',
      '/ai/scheduled-tasks/abc/runs',
      '/v2/groups/abc/time',
      '/v2/groups/abc/statistics/compliance',
      '/v2/times',
      '/system/settings',
      '/system/user-quotas/abc',
      '/v3/notifications/admin',
      '/v3/notifications/abc/versions',
      '/v3/ban/records',
      '/groups',
      '/groups/abc',
      '/v2/groups/abc'
    ]) {
      expect(isSmallJsonGetPath(path), path).toBe(true)
    }
    for (const path of [
      '/ai/conversations/abc/messages/extra',
      '/ai/models/abc',
      '/ai/logs/abc',
      '/ai/quota',
      '/ai/scheduled-tasks/abc',
      '/v2/groups/abc/time/extra',
      '/v2/times/abc',
      '/system/settings/abc',
      '/system/user-quotas',
      '/v3/notifications/abc',
      '/v3/ban/records/abc',
      '/attachments/abc/thumb',
      '/attachments/abc'
    ]) {
      expect(isSmallJsonGetPath(path), path).toBe(false)
    }
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

  it('首尔失败时仍由 LA 备用线路完成', async () => {
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

  it('首尔主线路快速返回 4xx 时立即结束，不等待备用线路', async () => {
    const primaryResponse = response({ detail: 'unauthorized' }, 401)
    const primary = new AxiosError(
      'unauthorized',
      AxiosError.ERR_BAD_REQUEST,
      config(),
      undefined,
      primaryResponse
    )
    let relayAborted = false
    const adapter: AxiosAdapter = async (request) => {
      if (request.baseURL !== 'https://content-la.example.test/api') {
        throw primary
      }
      await delay(60)
      relayAborted = request.signal?.aborted === true
      return response({ ok: true })
    }

    await expect(
      createHedgedGetAdapter(adapter, 'https://content-la.example.test/api')(config())
    ).rejects.toBe(primary)
    await delay(80)
    expect(relayAborted).toBe(true)
  })

  it('两路都失败时保留首尔主线路错误', async () => {
    const primary = new AxiosError('primary failed', AxiosError.ERR_NETWORK)
    const relay = new AxiosError('relay failed', AxiosError.ERR_NETWORK)
    const adapter = vi.fn<AxiosAdapter>(async (request) => {
      throw request.baseURL === 'https://content-la.example.test/api' ? relay : primary
    })

    await expect(
      createHedgedGetAdapter(adapter, 'https://content-la.example.test/api')(config())
    ).rejects.toBe(primary)
  })

  it('AI 请求的主线路 5xx 不会抢在备用线路 2xx 前获胜', async () => {
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      await delay(isRelay ? 10 : 1)
      return response(isRelay ? { ok: true } : { detail: 'bad gateway' }, isRelay ? 200 : 502)
    }
    const raceConfig = config({
      validateStatus: () => true
    })

    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api'
    )(raceConfig)
    expect(result.status).toBe(200)
    expect(result.data).toEqual({ ok: true })
  })

  it('AI 请求两路都不是 2xx 时保留主线路原始响应', async () => {
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      return response(isRelay ? { detail: 'relay' } : { detail: 'primary' }, 401)
    }
    const raceConfig = config({
      validateStatus: () => true
    })

    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api'
    )(raceConfig)
    expect(result.status).toBe(401)
    expect(result.data).toEqual({ detail: 'primary' })
  })
})

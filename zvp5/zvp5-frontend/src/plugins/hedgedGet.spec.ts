import { describe, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import {
  API_RACE_EVENT,
  apiRaceObservationPath,
  createHedgedGetAdapter,
  hedgedGetRelayBase,
  isSmallJsonGetPath,
  type ApiRaceObservation
} from './hedgedGet'

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

function nextRaceObservation(): Promise<ApiRaceObservation> {
  return new Promise((resolve) => {
    const listener = (event: Event) => {
      resolve((event as CustomEvent<ApiRaceObservation>).detail)
    }
    window.addEventListener(API_RACE_EVENT, listener, { once: true })
  })
}

describe('小型 GET 双路竞速', () => {
  it('只竞速白名单内的普通 JSON GET', () => {
    const primary = 'https://api-seoul.example.test/api'
    const relay = 'https://content-la.example.test/api'

    expect(hedgedGetRelayBase(config(), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ url: '/v1/notifications' }), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ method: 'post' }), primary, relay)).toBeNull()
    expect(hedgedGetRelayBase(config({ responseType: 'blob' }), primary, relay)).toBeNull()
    expect(hedgedGetRelayBase(config({ url: '/v1/users/me/time' }), primary, relay)).toBe(relay)
    expect(hedgedGetRelayBase(config({ url: '/v1/users/me/device' }), primary, relay)).toBe(relay)
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
      '/v1/notifications',
      '/v1/activities',
      '/v1/activities/abc',
      '/v1/activities/abc/members',
      '/v1/activities/abc/members/member',
      '/v1/statistics/activities/abc/analysis',
      '/v1/users',
      '/v1/users/abc/time',
      '/v1/users/abc/time_statistics',
      '/v1/users/abc/activities',
      '/v1/groups',
      '/v1/groups/abc/activities',
      '/v1/groups/abc/users',
      '/v1/users/me/device',
      '/v1/users/me/device-bindings',
      '/users/abc',
      '/v1/users/abc',
      '/v1/ban/rules',
      '/v1/ban/triggers',
      '/v1/ban/users',
      '/v1/ban/devices',
      '/v1/ban/violations',
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
      '/v1/groups/abc/time',
      '/v1/groups/abc/statistics/compliance',
      '/v1/times',
      '/system/settings',
      '/system/user-quotas/abc',
      '/v1/notifications/admin',
      '/v1/notifications/abc/versions',
      '/v1/ban/records',
      '/groups',
      '/groups/abc',
      '/v1/groups/abc'
    ]) {
      expect(isSmallJsonGetPath(path), path).toBe(true)
    }
    for (const path of [
      '/ai/conversations/abc/messages/extra',
      '/ai/models/abc',
      '/ai/logs/abc',
      '/ai/quota',
      '/ai/scheduled-tasks/abc',
      '/v1/groups/abc/time/extra',
      '/v1/times/abc',
      '/system/settings/abc',
      '/system/user-quotas',
      '/v1/notifications/abc',
      '/v1/ban/records/abc',
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

    const observed = nextRaceObservation()
    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      0
    )(config())
    const observation = await observed
    expect(result.data).toEqual({ source: 'relay' })
    await delay(80)
    expect(primaryAborted).toBe(true)
    expect(relayAborted).toBe(false)
    expect(observation).toMatchObject({
      path: '/version',
      winner: 'relay',
      reason: 'fastest_complete',
      selectedStatus: 200,
      relay: { origin: 'https://content-la.example.test', status: 200, outcome: 'success' },
      primary: { origin: 'https://api-seoul.example.test', outcome: 'superseded' }
    })
  })

  it('主路在门槛内完成时备路不启动（零双发）', async () => {
    const calls: string[] = []
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      calls.push(isRelay ? 'relay' : 'primary')
      await delay(isRelay ? 1 : 20)
      return response({ ok: true })
    }

    const observed = nextRaceObservation()
    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      80
    )(config())
    const observation = await observed
    await delay(200)
    expect(result.data).toEqual({ ok: true })
    expect(calls).toEqual(['primary'])
    expect(observation).toMatchObject({
      winner: 'primary',
      reason: 'fastest_complete',
      relay: { outcome: 'superseded' }
    })
  })

  it('主路超过门槛后备路对冲接管', async () => {
    const calls: string[] = []
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      calls.push(isRelay ? 'relay' : 'primary')
      const wait = isRelay ? 5 : 120
      await delay(wait)
      if (request.signal?.aborted) {
        throw new AxiosError('aborted', AxiosError.ERR_CANCELED, request)
      }
      return response(isRelay ? { source: 'relay' } : { source: 'primary' })
    }

    const observed = nextRaceObservation()
    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      40
    )(config())
    const observation = await observed
    expect(result.data).toEqual({ source: 'relay' })
    expect(calls.sort()).toEqual(['primary', 'relay'])
    expect(observation).toMatchObject({
      winner: 'relay',
      reason: 'fastest_complete',
      relay: { status: 200, outcome: 'success' }
    })
  })

  it('观测不包含查询串或鉴权凭证', async () => {
    const adapter: AxiosAdapter = async (request) => {
      await delay(request.baseURL === 'https://content-la.example.test/api' ? 1 : 30)
      return response({ ok: true })
    }
    const measuresBefore = performance.getEntriesByName(API_RACE_EVENT).length
    const observed = nextRaceObservation()
    await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      0
    )(
      config({
        url: '/ai/conversations/0123456789abcdef0123456789abcdef?token=secret-query',
        headers: { Authorization: 'Bearer secret-token', Cookie: 'session=secret-cookie' }
      })
    )
    const observation = await observed
    const serialized = JSON.stringify(observation)

    expect(observation.path).toBe('/ai/conversations/:id')
    expect(serialized).not.toContain('secret-query')
    expect(serialized).not.toContain('secret-token')
    expect(serialized).not.toContain('secret-cookie')
    expect(performance.getEntriesByName(API_RACE_EVENT).length).toBeGreaterThan(measuresBefore)
    expect(apiRaceObservationPath('/v1/users/68c3d9b346711c1f1f0475b3?x=1')).toBe('/v1/users/:id')
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
      'https://content-la.example.test/api',
      0
    )(config())
    expect(result.data).toEqual({ ok: true })
    expect(calls.sort()).toEqual(['primary', 'relay'])
  })

  it('后端主线路快速返回 4xx 时立即结束，不等待备用线路', async () => {
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

    const observed = nextRaceObservation()
    await expect(
      createHedgedGetAdapter(adapter, 'https://content-la.example.test/api', 0)(config())
    ).rejects.toBe(primary)
    const observation = await observed
    await delay(80)
    expect(relayAborted).toBe(true)
    expect(observation).toMatchObject({
      winner: 'primary',
      reason: 'primary_http_error',
      selectedStatus: 401,
      primary: { status: 401, outcome: 'http_error' },
      relay: { outcome: 'superseded' }
    })
  })

  it('两路都失败时保留后端主线路错误', async () => {
    const primary = new AxiosError('primary failed', AxiosError.ERR_NETWORK)
    const relay = new AxiosError('relay failed', AxiosError.ERR_NETWORK)
    const adapter = vi.fn<AxiosAdapter>(async (request) => {
      throw request.baseURL === 'https://content-la.example.test/api' ? relay : primary
    })

    const observed = nextRaceObservation()
    await expect(
      createHedgedGetAdapter(adapter, 'https://content-la.example.test/api', 0)(config())
    ).rejects.toBe(primary)
    const observation = await observed
    expect(observation).toMatchObject({
      winner: null,
      reason: 'both_failed',
      primary: { outcome: 'network_error' },
      relay: { outcome: 'network_error' }
    })
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

    const observed = nextRaceObservation()
    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      0
    )(raceConfig)
    const observation = await observed
    expect(result.status).toBe(200)
    expect(result.data).toEqual({ ok: true })
    expect(observation).toMatchObject({
      winner: 'relay',
      reason: 'fastest_complete',
      primary: { status: 502, outcome: 'http_error' },
      relay: { status: 200, outcome: 'success' }
    })
  })

  it('AI 请求两路都不是 2xx 时保留主线路原始响应', async () => {
    const adapter: AxiosAdapter = async (request) => {
      const isRelay = request.baseURL === 'https://content-la.example.test/api'
      return response(isRelay ? { detail: 'relay' } : { detail: 'primary' }, 401)
    }
    const raceConfig = config({
      validateStatus: () => true
    })

    const observed = nextRaceObservation()
    const result = await createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      0
    )(raceConfig)
    const observation = await observed
    expect(result.status).toBe(401)
    expect(result.data).toEqual({ detail: 'primary' })
    expect(observation).toMatchObject({
      winner: 'primary',
      reason: 'fallback_http_response',
      selectedStatus: 401,
      primary: { status: 401, outcome: 'http_error' },
      relay: { status: 401, outcome: 'http_error' }
    })
  })

  it('用户取消时两路都记录为 cancelled', async () => {
    const controller = new AbortController()
    const adapter: AxiosAdapter = async (request) => {
      await delay(80)
      if (request.signal?.aborted) {
        throw new AxiosError('aborted', AxiosError.ERR_CANCELED, request)
      }
      return response({ ok: true })
    }
    const observed = nextRaceObservation()
    const pending = createHedgedGetAdapter(
      adapter,
      'https://content-la.example.test/api',
      0
    )(config({ signal: controller.signal })).catch((error) => error)
    await delay(5)
    controller.abort()
    await pending
    const observation = await observed

    expect(observation).toMatchObject({
      winner: null,
      reason: 'user_cancelled',
      primary: { outcome: 'cancelled' },
      relay: { outcome: 'cancelled' }
    })
  })
})

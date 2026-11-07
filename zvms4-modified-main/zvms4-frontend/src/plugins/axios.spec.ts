import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import { Blob as NodeBlob } from 'node:buffer'
import client from './axios'
import { cacheGet, cacheSet } from '@/utils/idbCache'
import { clearMemoryCache } from '@/utils/requestCache'

const { elMessageMock } = vi.hoisted(() => ({ elMessageMock: vi.fn() }))

const { abortAllAiRunsMock } = vi.hoisted(() => ({ abortAllAiRunsMock: vi.fn() }))

vi.mock('@/router', () => ({ default: { currentRoute: { value: { path: '/' } }, push: vi.fn(), replace: vi.fn() } }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ removeUser: vi.fn().mockResolvedValue(undefined) }) }))
vi.mock('@/api/ai/activeRuns', () => ({
  abortAllAiRuns: abortAllAiRunsMock,
  registerAiAbort: vi.fn(() => () => {}),
  activeAiRunCount: vi.fn(() => 0)
}))
vi.mock('@/utils/idbCache', () => ({ cacheGet: vi.fn().mockResolvedValue(null), cacheSet: vi.fn() }))
vi.mock('@/plugins/ua', () => ({ getXuehaiId: () => 0 }))
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  // 进程内缓存是模块级状态，不清理会让相邻用例互相命中。
  clearMemoryCache()
})
afterEach(() => { vi.unstubAllGlobals() })

/** 抓取 ElMessage 实际收到的 message(每个元素对应一次弹窗)。 */
const shownMessages = () => elMessageMock.mock.calls.map((call) => call[0]?.message)

/**
 * 触发响应拦截器错误分支用的 adapter。axios 只在 adapter 内部 settle 非 2xx,
 * 所以必须抛 AxiosError 并带上 response;若 adapter resolve 一个 410 响应,
 * 拦截器会走成功分支。
 */
const failingAdapter = (status: number, data: unknown): AxiosAdapter => async (config) => {
  const response: AxiosResponse = { data, status, statusText: 'Error', headers: {}, config }
  throw new AxiosError('request failed', 'ERR_BAD_REQUEST', config, undefined, response)
}

/**
 * jsdom 26 的 Blob 没有实现 text()/arrayBuffer()(真实浏览器均有;jsdom 的 Blob 只暴露了
 * slice),直接 new Blob([...]) 会让被测代码拿到不存在的方法而回落兜底。这里把全局 Blob
 * 换成 node:buffer 的规范实现,使被测代码走的是生产环境真实存在的 .text() 分支。
 */
const useSpecCompliantBlob = () => vi.stubGlobal('Blob', NodeBlob)

/**
 * 构造与拦截器同一个 realm 的 ArrayBuffer。Node 的 TextEncoder 产出的是 Node realm
 * 的 ArrayBuffer,而 jsdom 环境下的全局 ArrayBuffer 是另一个 realm 的构造器,
 * `instanceof` 为 false;用全局构造器分配再拷贝字节,等价于浏览器(单一 realm)里
 * `new TextEncoder().encode(json).buffer` 的行为。
 */
function jsonArrayBuffer(json: string): ArrayBuffer {
  const encoded = new TextEncoder().encode(json)
  const body = new ArrayBuffer(encoded.length)
  new Uint8Array(body).set(encoded)
  return body
}

/** text() 抛错的 Blob,用于验证解包异常不改变原有错误流。 */
class UnreadableBlob extends NodeBlob {
  text(): Promise<string> {
    return Promise.reject(new Error('read failed'))
  }
}

it('serves a repeated authenticated GET from cache without a second request', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  const first = await client.get('/v2/users/alice')
  const second = await client.get('/v2/users/alice')

  expect(calls).toBe(1)
  expect(first.data).toEqual({ calls: 1 })
  expect(second.data).toEqual({ calls: 1 })
  expect(cacheSet).toHaveBeenCalledTimes(1)
})

it('keeps cache entries isolated per account', async () => {
  localStorage.setItem('token', 'alice-session-token')
  client.defaults.adapter = async (config) => ({
    data: { owner: 'alice' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  })
  await client.get('/v2/users/self')

  localStorage.setItem('token', 'bob-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { owner: 'bob' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  const result = await client.get('/v2/users/self')

  // 换账号后旧缓存必须失效，请求重新发出并返回新身份的数据。
  expect(calls).toBe(1)
  expect(result.data).toEqual({ owner: 'bob' })

  const keys = vi.mocked(cacheSet).mock.calls.map(([key]) => String(key))
  expect(keys).toHaveLength(2)
  expect(keys[0]).toContain('/v2/users/self')
  expect(keys[0]).not.toBe(keys[1])
  // 键里只留 token 指纹，不能出现凭证原文。
  expect(keys.join('|')).not.toContain('alice-session-token')
  expect(keys.join('|')).not.toContain('bob-session-token')
})

it('does not reuse the anonymous cache after logging in', async () => {
  client.defaults.adapter = async (config) => ({
    data: { owner: 'anonymous' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  })
  await client.get('/v2/users/self')

  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { owner: 'alice' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  const result = await client.get('/v2/users/self')

  expect(calls).toBe(1)
  expect(result.data).toEqual({ owner: 'alice' })
})

it('falls back to its own identity cache when the authenticated GET fails', async () => {
  localStorage.setItem('token', 'bob-session-token')
  vi.mocked(cacheGet).mockResolvedValueOnce({ t: Date.now(), data: { cached: true } })
  client.defaults.adapter = async (config) => {
    throw new AxiosError('offline', 'ERR_NETWORK', config)
  }

  const result = await client.get('/v2/users/self')

  expect(result.data).toEqual({ cached: true })
  const key = String(vi.mocked(cacheGet).mock.calls[0][0])
  expect(key).toContain('/v2/users/self')
  expect(key).not.toContain('bob-session-token')
})

it('keeps the existing exclusion list for authenticated GETs', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  await client.get('/system/settings')
  await client.get('/system/settings')

  expect(calls).toBe(2)
  expect(cacheSet).not.toHaveBeenCalled()
})

it('preserves error rejection for unauthorized responses', async () => {
  client.defaults.adapter = async (config) => {
    const response: AxiosResponse = { data: { detail: 'expired' }, status: 401, statusText: 'Unauthorized', headers: {}, config }
    throw new AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined, response)
  }
  await expect(client.get('/users/me')).rejects.toThrow('expired')
})

// --- 非 blob 的 JSON 错误体:既有行为不得回归 ---------------------------------

it('shows the localized message of a structured JSON detail', async () => {
  const detail = { code: 'GONE', message: 'Attachment unavailable' }
  client.defaults.adapter = failingAdapter(410, { detail })
  const error = await client.get('/attachments/abc/thumb').catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(410)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['附件暂不可用'])
})

it('falls back to the generic Chinese hint when a JSON error body carries no detail', async () => {
  client.defaults.adapter = failingAdapter(400, { message: 'no detail field here' })
  await client.get('/activities').catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['请求失败，请稍后重试'])
})

it('emits exactly one grouped error toast for an ordinary JSON failure', async () => {
  client.defaults.adapter = failingAdapter(400, { detail: 'bad request' })
  await client.get('/activities').catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(elMessageMock.mock.calls[0][0]).toMatchObject({
    message: 'bad request',
    type: 'error',
    grouping: true,
    plain: true
  })
})

// --- Blob / ArrayBuffer 错误体:解包后必须取到真实 detail ----------------------

it('unwraps a Blob JSON error body to its detail and still rejects', async () => {
  useSpecCompliantBlob()
  const body = new Blob([JSON.stringify({ detail: 'Attachment unavailable' })], { type: 'application/json' })
  client.defaults.adapter = failingAdapter(410, body)
  const error = await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.message).toBe('request failed')
  expect(error.response?.status).toBe(410)
  expect(error.response?.data).toBe(body)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['附件暂不可用'])
})

it('unwraps an ArrayBuffer JSON error body to its detail and still rejects', async () => {
  const body = jsonArrayBuffer(JSON.stringify({ detail: 'Attachment unavailable' }))
  expect(body).toBeInstanceOf(ArrayBuffer)
  client.defaults.adapter = failingAdapter(410, body)
  const error = await client.get('/attachments/abc/bytes', { responseType: 'arraybuffer' }).catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(410)
  expect(error.response?.data).toBe(body)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['附件暂不可用'])
})

it('uses short non-JSON Blob body text as the message', async () => {
  useSpecCompliantBlob()
  client.defaults.adapter = failingAdapter(410, new Blob(['boom'], { type: 'text/plain' }))
  await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['boom'])
})

it('falls back to the generic Chinese hint for a long non-JSON Blob body (>300 chars)', async () => {
  useSpecCompliantBlob()
  const html = '<!doctype html><html><body>' + 'filler '.repeat(60) + '</body></html>'
  expect(html.length).toBeGreaterThan(300)
  client.defaults.adapter = failingAdapter(410, new Blob([html], { type: 'text/html' }))
  await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['请求失败，请稍后重试'])
})

it('keeps the fallback and the rejection when reading a Blob body throws', async () => {
  useSpecCompliantBlob()
  const body = new UnreadableBlob(['{"detail":"must not surface"}'], { type: 'application/json' })
  client.defaults.adapter = failingAdapter(410, body)
  const error = await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(410)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['请求失败，请稍后重试'])
})

// --- 登录设备闸的结构化 403:登录页接管,拦截器不弹通用 toast ------------------

const deviceDetailCodes = [
  'DEVICE_REQUIRED',
  'DEVICE_BANNED',
  'DEVICE_NOT_PRIMARY',
  'TEMP_PASSWORD_INVALID',
  'DEVICE_CHALLENGE_MISMATCH'
]

it.each(deviceDetailCodes)('keeps a structured %s 403 silent and still rejects', async (code) => {
  client.defaults.adapter = failingAdapter(403, {
    detail: { code, message: '人话提示', challenge: 'ch-1' }
  })
  const error = await client.post('/users/auth', {}).catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(403)
  expect(elMessageMock).not.toHaveBeenCalled()
})

it('still toasts a string-detail 403 (unchanged behavior)', async () => {
  client.defaults.adapter = failingAdapter(403, { detail: 'WRONG_PASSWORD' })
  await client.post('/users/auth', {}).catch((e) => e)
  expect(shownMessages()).toEqual(['WRONG_PASSWORD'])
})

// --- 封禁 403:除遮罩/store 之外,必须中止在途 AI 请求 -------------------------

it('aborts in-flight AI runs when a 403 USER_BANNED lands', async () => {
  client.defaults.adapter = failingAdapter(403, { detail: 'USER_BANNED' })
  const error = await client.get('/activities').catch((e) => e)

  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(403)
  expect(abortAllAiRunsMock).toHaveBeenCalledWith('banned')
  // 封禁分支保持静默:不弹通用 toast。
  expect(elMessageMock).not.toHaveBeenCalled()
})

it('does not touch AI runs for an ordinary 403', async () => {
  client.defaults.adapter = failingAdapter(403, { detail: 'WRONG_PASSWORD' })
  await client.post('/users/auth', {}).catch((e) => e)
  expect(abortAllAiRunsMock).not.toHaveBeenCalled()
})

it('keeps the ban branch and the rejection when aborting throws', async () => {
  abortAllAiRunsMock.mockImplementationOnce(() => {
    throw new Error('abort exploded')
  })
  client.defaults.adapter = failingAdapter(403, { detail: 'USER_BANNED' })
  const error = await client.get('/activities').catch((e) => e)

  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(403)
  expect(elMessageMock).not.toHaveBeenCalled()
})

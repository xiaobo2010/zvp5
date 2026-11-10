import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import { Blob as NodeBlob } from 'node:buffer'
import client, { baseURLForRequest, resetUserInteractionCacheBypass } from './axios'
import { cacheGet, cacheSet } from '@/utils/idbCache'
import { clearMemoryCache } from '@/utils/requestCache'
import { clearFlights, pendingFlightCount } from './singleFlight'

const { elMessageMock } = vi.hoisted(() => ({ elMessageMock: vi.fn() }))

const { abortAllAiRunsMock } = vi.hoisted(() => ({ abortAllAiRunsMock: vi.fn() }))

/** 学海设备号：0 = 普通浏览器，其余为学海设备。 */
const { xuehaiIdMock } = vi.hoisted(() => ({ xuehaiIdMock: { value: 0 } }))

vi.mock('@/router', () => ({ default: { currentRoute: { value: { path: '/' } }, push: vi.fn(), replace: vi.fn() } }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ removeUser: vi.fn().mockResolvedValue(undefined) }) }))
vi.mock('@/api/ai/activeRuns', () => ({
  abortAllAiRuns: abortAllAiRunsMock,
  registerAiAbort: vi.fn(() => () => {}),
  activeAiRunCount: vi.fn(() => 0)
}))
vi.mock('@/utils/idbCache', () => ({ cacheGet: vi.fn().mockResolvedValue(null), cacheSet: vi.fn() }))
vi.mock('@/plugins/ua', () => ({ getXuehaiId: () => xuehaiIdMock.value }))
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  xuehaiIdMock.value = 0
  // 进程内缓存是模块级状态，不清理会让相邻用例互相命中。
  clearMemoryCache()
  resetUserInteractionCacheBypass()
  // 在途合并的航班表同样是模块级状态。
  clearFlights()
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

  const first = await client.get('/v1/users/alice')
  const second = await client.get('/v1/users/alice')

  expect(calls).toBe(1)
  expect(first.data).toEqual({ calls: 1 })
  expect(second.data).toEqual({ calls: 1 })
  expect(cacheSet).toHaveBeenCalledTimes(1)
})

it('bypasses the process cache when a button interaction requests fresh data', async () => {
  localStorage.setItem('token', 'alice-session-token')
  const button = document.createElement('button')
  document.body.appendChild(button)
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  const initial = await client.get('/v1/users/alice')
  button.click()
  const refreshed = await client.get('/v1/users/alice')
  resetUserInteractionCacheBypass()
  const afterRefresh = await client.get('/v1/users/alice')

  expect(initial.data).toEqual({ calls: 1 })
  expect(refreshed.data).toEqual({ calls: 2 })
  expect(afterRefresh.data).toEqual({ calls: 2 })
  expect(calls).toBe(2)
  button.remove()
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
  await client.get('/v1/users/self')

  localStorage.setItem('token', 'bob-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { owner: 'bob' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  const result = await client.get('/v1/users/self')

  // 换账号后旧缓存必须失效，请求重新发出并返回新身份的数据。
  expect(calls).toBe(1)
  expect(result.data).toEqual({ owner: 'bob' })

  const keys = vi.mocked(cacheSet).mock.calls.map(([key]) => String(key))
  expect(keys).toHaveLength(2)
  expect(keys[0]).toContain('/v1/users/self')
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
  await client.get('/v1/users/self')

  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    return { data: { owner: 'alice' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  const result = await client.get('/v1/users/self')

  expect(calls).toBe(1)
  expect(result.data).toEqual({ owner: 'alice' })
})

it('falls back to its own identity cache when the authenticated GET fails', async () => {
  localStorage.setItem('token', 'bob-session-token')
  vi.mocked(cacheGet).mockResolvedValueOnce({ t: Date.now(), data: { cached: true } })
  client.defaults.adapter = async (config) => {
    throw new AxiosError('offline', 'ERR_NETWORK', config)
  }

  const result = await client.get('/v1/users/self')

  expect(result.data).toEqual({ cached: true })
  const key = String(vi.mocked(cacheGet).mock.calls[0][0])
  expect(key).toContain('/v1/users/self')
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

// 登录页自己弹 ZAuthAlert 呈现失败原因时带这个标记：拦截器不能再叠一条通用 toast。
it('suppresses the generic toast when the caller marked the request', async () => {
  client.defaults.adapter = failingAdapter(403, { detail: '用户名或密码错误' })
  const error = await client
    .post('/users/auth', {}, { suppressErrorToast: true })
    .catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(403)
  expect(elMessageMock).not.toHaveBeenCalled()
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

// --- 在途合并（single-flight）:并发的相同 GET 只发一次网络请求 -----------------
// 跨域请求的 CORS 预检按完整 URL（含 query）与请求头集合分别缓存，冷缓存下并发
// N 个相同请求会打 N 次预检；合并后预检与真实请求都只剩一次。

it('merges concurrent identical GETs into a single request', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    await new Promise((resolve) => setTimeout(resolve, 10))
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  const results = await Promise.all([
    client.get('/v1/users/me/device'),
    client.get('/v1/users/me/device'),
    client.get('/v1/users/me/device')
  ])

  expect(calls).toBe(1)
  for (const result of results) {
    expect(result.data).toEqual({ calls: 1 })
  }
  expect(pendingFlightCount()).toBe(0)
})

it('keeps concurrent GETs with different query strings apart', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    await new Promise((resolve) => setTimeout(resolve, 10))
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  await Promise.all([
    client.get('/v1/users', { params: { page: 1 } }),
    client.get('/v1/users', { params: { page: 2 } })
  ])

  expect(calls).toBe(2)
})

it('never merges write requests', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    await new Promise((resolve) => setTimeout(resolve, 10))
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  await Promise.all([client.post('/v1/notifications', {}), client.post('/v1/notifications', {})])

  expect(calls).toBe(2)
})

it('never merges requests that carry a caller-side abort signal', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    await new Promise((resolve) => setTimeout(resolve, 10))
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  const controller = new AbortController()
  await Promise.all([
    client.get('/v1/users/self', { signal: controller.signal }),
    client.get('/v1/users/self', { signal: controller.signal })
  ])

  expect(calls).toBe(2)
})

it('gives every caller its own copy of the shared payload', async () => {
  localStorage.setItem('token', 'alice-session-token')
  client.defaults.adapter = async (config) => ({
    data: { list: [1, 2] },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  })

  const [first, second] = await Promise.all([
    client.get('/v1/users/self'),
    client.get('/v1/users/self')
  ])

  first.data.list.push(3)
  expect(second.data.list).toEqual([1, 2])
})

it('rejects every merged caller when the shared request fails, with a single toast', async () => {
  localStorage.setItem('token', 'alice-session-token')
  client.defaults.adapter = failingAdapter(500, { detail: 'boom' })

  const results = await Promise.allSettled([
    client.get('/v1/users/self'),
    client.get('/v1/users/self')
  ])

  expect(results.map((r) => r.status)).toEqual(['rejected', 'rejected'])
  // 跟随者不重复弹窗：错误由发起者那一次请求统一处理。
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(pendingFlightCount()).toBe(0)
})

it('ends the merged flight when the shared request throws a non-axios error', async () => {
  localStorage.setItem('token', 'alice-session-token')
  client.defaults.adapter = async () => {
    throw new Error('adapter exploded')
  }

  const results = await Promise.allSettled([
    client.get('/v1/users/self'),
    client.get('/v1/users/self')
  ])

  expect(results.map((r) => r.status)).toEqual(['rejected', 'rejected'])
  // 航班必须结束：跟随者换掉了 axios 自带 adapter（连带丢掉请求超时），
  // 航班不结束跟随者就会一直挂着。
  expect(pendingFlightCount()).toBe(0)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
})

it('lets a merged caller receive the retried result of the shared request', async () => {  localStorage.setItem('token', 'alice-session-token')
  let calls = 0
  client.defaults.adapter = async (config) => {
    calls += 1
    if (calls === 1) {
      throw new AxiosError('offline', 'ERR_NETWORK', config)
    }
    return { data: { calls }, status: 200, statusText: 'OK', headers: {}, config }
  }

  const [first, second] = await Promise.all([
    client.get('/v1/users/self'),
    client.get('/v1/users/self')
  ])

  expect(calls).toBe(2)
  expect(first.data).toEqual({ calls: 2 })
  expect(second.data).toEqual({ calls: 2 })
})

it('writes the response cache once for a merged group', async () => {
  localStorage.setItem('token', 'alice-session-token')
  client.defaults.adapter = async (config) => ({
    data: { owner: 'alice' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  })

  await Promise.all([client.get('/v1/users/self'), client.get('/v1/users/self')])

  expect(cacheSet).toHaveBeenCalledTimes(1)
})

// --- 请求头集合恒定:解锁凭证不再让预检缓存整片失效 -----------------------------

it('always sends the device unlock header on a xuehai device', async () => {
  xuehaiIdMock.value = 123
  localStorage.setItem('token', 'alice-session-token')
  let headers: Record<string, unknown> = {}
  client.defaults.adapter = async (config) => {
    headers = config.headers as unknown as Record<string, unknown>
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
  }

  await client.get('/v1/users/self')

  expect(headers['Xuehai-Id']).toBe('123')
  // 没有解锁票据时也发空值头：请求头集合保持恒定，浏览器才不会在解锁那一刻
  // 把已知的每个 URL 重新预检一遍。
  expect('X-Device-Unlock' in headers).toBe(true)
  expect(headers['X-Device-Unlock']).toBe('')
})

it('sends no device headers on a regular browser', async () => {
  localStorage.setItem('token', 'alice-session-token')
  let headers: Record<string, unknown> = {}
  client.defaults.adapter = async (config) => {
    headers = config.headers as unknown as Record<string, unknown>
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
  }

  await client.get('/v1/users/self')

  expect('Xuehai-Id' in headers).toBe(false)
  expect('X-Device-Unlock' in headers).toBe(false)
})

it('routes file endpoints to the content host derived from the page origin', () => {
  // 预览站由 VITE_API_CONTENT_BASE 覆盖这条推导（见 utils/apiBaseConfig.spec.ts）。
  expect(baseURLForRequest('/system/files/list')).toBe(`${window.location.origin}/api`)
  expect(baseURLForRequest('/v1/users/me')).not.toBe(`${window.location.origin}/api`)
})

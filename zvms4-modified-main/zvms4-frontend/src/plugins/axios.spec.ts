import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios'
import { Blob as NodeBlob } from 'node:buffer'
import client from './axios'
import { cacheGet, cacheSet } from '@/utils/idbCache'

const { elMessageMock } = vi.hoisted(() => ({ elMessageMock: vi.fn() }))

vi.mock('@/router', () => ({ default: { currentRoute: { value: { path: '/' } }, push: vi.fn(), replace: vi.fn() } }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ removeUser: vi.fn().mockResolvedValue(undefined) }) }))
vi.mock('@/utils/idbCache', () => ({ cacheGet: vi.fn().mockResolvedValue(null), cacheSet: vi.fn() }))
vi.mock('@/plugins/ua', () => ({ getXuehaiId: () => 0 }))
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))
vi.mock('nprogress', () => ({ default: { start: vi.fn(), done: vi.fn() } }))

beforeEach(() => { vi.clearAllMocks(); localStorage.clear() })
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

// --- 非 blob 的 JSON 错误体:既有行为不得回归 ---------------------------------

it('stringifies a non-string JSON detail (unchanged behavior)', async () => {
  const detail = { code: 'GONE', message: 'Attachment unavailable' }
  client.defaults.adapter = failingAdapter(410, { detail })
  const error = await client.get('/attachments/abc/thumb').catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(410)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual([JSON.stringify(detail)])
})

it("falls back to '未知错误' when a JSON error body carries no detail (unchanged behavior)", async () => {
  client.defaults.adapter = failingAdapter(400, { message: 'no detail field here' })
  await client.get('/activities').catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['未知错误'])
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
  expect(shownMessages()).toEqual(['Attachment unavailable'])
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
  expect(shownMessages()).toEqual(['Attachment unavailable'])
})

it('uses short non-JSON Blob body text as the message', async () => {
  useSpecCompliantBlob()
  client.defaults.adapter = failingAdapter(410, new Blob(['boom'], { type: 'text/plain' }))
  await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['boom'])
})

it("falls back to '未知错误' for a long non-JSON Blob body (>300 chars)", async () => {
  useSpecCompliantBlob()
  const html = '<!doctype html><html><body>' + 'filler '.repeat(60) + '</body></html>'
  expect(html.length).toBeGreaterThan(300)
  client.defaults.adapter = failingAdapter(410, new Blob([html], { type: 'text/html' }))
  await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['未知错误'])
})

it('keeps the fallback and the rejection when reading a Blob body throws', async () => {
  useSpecCompliantBlob()
  const body = new UnreadableBlob(['{"detail":"must not surface"}'], { type: 'application/json' })
  client.defaults.adapter = failingAdapter(410, body)
  const error = await client.get('/attachments/abc/thumb', { responseType: 'blob' }).catch((e) => e)
  expect(error).toBeInstanceOf(AxiosError)
  expect(error.response?.status).toBe(410)
  expect(elMessageMock).toHaveBeenCalledTimes(1)
  expect(shownMessages()).toEqual(['未知错误'])
})

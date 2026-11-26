import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { aiFetchJson, aiRequest, aiRequestVoid } from './http'
import { AiRequestError } from './errors'

/**
 * AI 请求层自己的封禁扇出。
 *
 * 背景：http.ts 把 validateStatus 放开成「全部成功」，非 2xx 不会进 axios 拦截器的
 * 403 分支。所以封禁 403 必须在 request() 里补一次同样的处理（写 ban 状态 +
 * abortAllAiRuns('banned')），同时保持原有错误抛出不变。
 */

const { axiosMock, abortAllAiRunsMock, storeMock } = vi.hoisted(() => ({
  axiosMock: vi.fn(),
  abortAllAiRunsMock: vi.fn(),
  storeMock: {
    isLogin: true,
    bannedNoticeAt: 0,
    banReason: '',
    banUntil: 0
  }
}))

vi.mock('@/plugins/axios', () => ({ default: axiosMock, baseURL: '/api' }))
vi.mock('@/api/ai/activeRuns', () => ({
  abortAllAiRuns: abortAllAiRunsMock,
  registerAiAbort: vi.fn(() => () => {}),
  activeAiRunCount: vi.fn(() => 0)
}))
vi.mock('@/stores/user', () => ({ useUserStore: () => storeMock }))

/** zvp 面响应替身：只实现 request() 读到的字段。 */
function fakeAxiosResponse(status: number, data: unknown, headers: Record<string, string> = {}) {
  return { status, data, headers, statusText: String(status) }
}

/** 捕获并断言为 AiRequestError：测试里既能看字段，又不用每个 catch 标类型。 */
async function aiErrorOf(promise: Promise<unknown>): Promise<AiRequestError> {
  const caught = await promise.catch((e: unknown) => e)
  expect(caught).toBeInstanceOf(AiRequestError)
  return caught as AiRequestError
}

beforeEach(() => {
  vi.clearAllMocks()
  storeMock.isLogin = true
  storeMock.bannedNoticeAt = 0
  storeMock.banReason = ''
  storeMock.banUntil = 0
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('zvp 面封禁 403', () => {
  it('USER_BANNED：写 ban 状态、中止在途 AI 请求，并照旧抛 AiRequestError', async () => {
    axiosMock.mockResolvedValue(
      fakeAxiosResponse(
        403,
        { detail: 'USER_BANNED' },
        {
          'x-ban-reason': encodeURIComponent('发布垃圾内容'),
          'x-ban-until': '1893456000'
        }
      )
    )

    const error = await aiErrorOf(aiRequest({ method: 'get', url: '/ai/keys' }))

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error.status).toBe(403)
    expect(error.code).toBe('USER_BANNED')
    expect(abortAllAiRunsMock).toHaveBeenCalledWith('banned')
    expect(storeMock.bannedNoticeAt).toBeGreaterThan(0)
    expect(storeMock.banReason).toBe('发布垃圾内容')
    expect(storeMock.banUntil).toBe(1893456000 * 1000)
  })

  it('结构化 detail.code 的 USER_BANNED 同样命中（判定看错误码，不看 detail 形状）', async () => {
    axiosMock.mockResolvedValue(
      fakeAxiosResponse(403, { detail: { code: 'USER_BANNED', message: '已被封禁' } })
    )

    const error = await aiErrorOf(aiRequestVoid({ method: 'delete', url: '/ai/keys/k1' }))

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error.code).toBe('USER_BANNED')
    expect(abortAllAiRunsMock).toHaveBeenCalledWith('banned')
    // 头缺失时回落「空原因 + 永久(0)」，与后端契约一致。
    expect(storeMock.banReason).toBe('')
    expect(storeMock.banUntil).toBe(0)
  })

  it('重复命中不重置已有倒计时（幂等）', async () => {
    storeMock.bannedNoticeAt = 111
    axiosMock.mockResolvedValue(fakeAxiosResponse(403, { detail: 'USER_BANNED' }))

    await aiRequest({ method: 'get', url: '/ai/quota' }).catch((e) => e)

    expect(storeMock.bannedNoticeAt).toBe(111)
    expect(abortAllAiRunsMock).toHaveBeenCalledWith('banned')
  })

  it('普通 403（非 USER_BANNED）：不中止、不写 ban 状态，仍抛原错误', async () => {
    axiosMock.mockResolvedValue(fakeAxiosResponse(403, { detail: 'AI_FORBIDDEN' }))

    const error = await aiErrorOf(aiRequest({ method: 'get', url: '/ai/keys' }))

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error.code).toBe('AI_FORBIDDEN')
    expect(abortAllAiRunsMock).not.toHaveBeenCalled()
    expect(storeMock.bannedNoticeAt).toBe(0)
    expect(storeMock.banReason).toBe('')
  })

  it('中止扇出抛错不影响原错误抛出与类型', async () => {
    abortAllAiRunsMock.mockImplementationOnce(() => {
      throw new Error('abort exploded')
    })
    axiosMock.mockResolvedValue(fakeAxiosResponse(403, { detail: 'USER_BANNED' }))

    const error = await aiErrorOf(aiRequest({ method: 'get', url: '/ai/keys' }))

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error.status).toBe(403)
    expect(error.code).toBe('USER_BANNED')
  })
})

describe('OpenAI 面（fetch）封禁 403', () => {
  function fakeFetchResponse(status: number, body: unknown, banReason: string | null) {
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get: (name: string) => {
          const key = name.toLowerCase()
          if (key === 'x-ban-reason') {
            return banReason
          }
          if (key === 'x-ban-until') {
            return ''
          }
          return null
        }
      },
      json: async () => body
    }
  }

  it('aiFetchJson 撞上 USER_BANNED：同样扇出', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeFetchResponse(403, { error: { code: 'USER_BANNED', message: 'banned' } }, '刷接口')
      )
    )

    const error = await aiErrorOf(aiFetchJson('/ai/v1/models'))

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error.code).toBe('USER_BANNED')
    expect(abortAllAiRunsMock).toHaveBeenCalledWith('banned')
    expect(storeMock.banReason).toBe('刷接口')
    expect(storeMock.banUntil).toBe(0)
  })

  it('OpenAI 面普通 403：不扇出', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeFetchResponse(403, { error: { code: 'AI_FORBIDDEN', message: 'nope' } }, null)
      )
    )

    await aiFetchJson('/ai/v1/models').catch((e) => e)

    expect(abortAllAiRunsMock).not.toHaveBeenCalled()
    expect(storeMock.bannedNoticeAt).toBe(0)
  })
})

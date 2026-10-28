import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AI_PROMPT_DAYS_DEFAULT,
  AI_PROMPT_DAYS_MAX,
  AI_PROMPTS_PERPAGE_MAX,
  getAiPrompt,
  listAiPrompts
} from './prompts'
import { AiRequestError } from './errors'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

const metaFixture = {
  oid: '69b7a4ad0243d973f1e1a2c1',
  user_oid: '69b7a4ad0243d973f1e1a2b2',
  user_name: '张三',
  purpose: 'chat',
  roles_snapshot: 'admin,department',
  prompt_chars: 1280,
  created_at: '2026-10-24T08:00:00'
}

const detailFixture = {
  oid: '69b7a4ad0243d973f1e1a2c1',
  request_id: 'req-0001',
  user_oid: '69b7a4ad0243d973f1e1a2b2',
  user_name: '张三',
  purpose: 'chat',
  roles_snapshot: 'admin,department',
  prompt_text: '你好，帮我查一下活动。',
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

function paramsOf(index = 0): Record<string, unknown> {
  return axiosMock.mock.calls[index]![0].params as Record<string, unknown>
}

beforeEach(() => {
  axiosMock.mockReset()
})

describe('prompt 审阅 API 契约', () => {
  it('默认带上第一页、每页 20 条与 7 天窗口', async () => {
    axiosMock.mockResolvedValueOnce(envelope([metaFixture], 1))

    await listAiPrompts()

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'get',
        url: '/ai/prompts',
        params: { page: 1, perpage: 20, days: AI_PROMPT_DAYS_DEFAULT }
      })
    )
  })

  it('用户 oid 去空白后传给后端；空白值不落到 query string', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ userOid: '  uid-1  ' })
    expect(paramsOf().user_oid).toBe('uid-1')

    axiosMock.mockReset()
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ userOid: '   ' })
    expect(paramsOf().user_oid).toBeUndefined()
  })

  it('天数被夹到 1..90（与正文留存期一致）', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ days: 500 })
    expect(paramsOf().days).toBe(AI_PROMPT_DAYS_MAX)

    axiosMock.mockReset()
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ days: 0 })
    expect(paramsOf().days).toBe(1)
  })

  it('每页条数被夹到上限以内', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAiPrompts({ perpage: 5000 })

    expect(paramsOf()).toMatchObject({ page: 1, perpage: AI_PROMPTS_PERPAGE_MAX })
  })

  it('场景与关键字去空白后传给后端；空白值不落到 query string', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ purpose: ' ocr ', keyword: '  本月活动  ' })
    expect(paramsOf()).toMatchObject({ purpose: 'ocr', keyword: '本月活动' })

    axiosMock.mockReset()
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ purpose: '  ', keyword: '   ' })
    expect(paramsOf().purpose).toBeUndefined()
    expect(paramsOf().keyword).toBeUndefined()
  })

  it('给了任一时间边界就整段忽略 days，边界折成 epoch 秒', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({
      days: 30,
      time_start: new Date('2026-10-01T00:00:00Z'),
      time_end: new Date('2026-10-24T00:00:00Z')
    })

    const params = paramsOf()
    expect(params.days).toBeUndefined()
    expect(params.time_start).toBe(
      Math.floor(new Date('2026-10-01T00:00:00Z').getTime() / 1000)
    )
    expect(params.time_end).toBe(Math.floor(new Date('2026-10-24T00:00:00Z').getTime() / 1000))
  })

  it('只给一侧边界时同样不传 days', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiPrompts({ time_start: 1790812800 })

    expect(paramsOf()).toMatchObject({ time_start: 1790812800 })
    expect(paramsOf().days).toBeUndefined()
  })

  it('metadata.size 作为总数读出', async () => {
    axiosMock.mockResolvedValueOnce(envelope([metaFixture], 137))

    const result = await listAiPrompts()

    expect(result.total).toBe(137)
    expect(result.rows).toEqual([metaFixture])
  })

  it('错误响应抛出带错误码的 AI 错误', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 400,
      data: { detail: 'Invalid Object ID' },
      headers: {}
    })

    const error = await listAiPrompts({ userOid: 'nope' }).catch((err: unknown) => err)

    expect(error).toBeInstanceOf(AiRequestError)
    expect(error).toMatchObject({ status: 400, code: 'Invalid Object ID' })
  })
})

describe('prompt 正文 API 契约', () => {
  it('按 oid 拼路径，并编码特殊字符', async () => {
    axiosMock.mockResolvedValueOnce(envelope(detailFixture, 1))

    await getAiPrompt('a/b')

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'get', url: '/ai/prompts/a%2Fb' })
    )
  })

  it('详情原样返回元数据与正文', async () => {
    axiosMock.mockResolvedValueOnce(envelope(detailFixture, 1))

    const result = await getAiPrompt(detailFixture.oid)

    expect(result.prompt_text).toBe('你好，帮我查一下活动。')
    expect(result.request_id).toBe('req-0001')
  })

  it('正文缺失时收敛成空串：页面只需判断「有没有内容」', async () => {
    axiosMock.mockResolvedValueOnce(
      envelope({ ...detailFixture, prompt_text: null }, 1)
    )

    const result = await getAiPrompt(detailFixture.oid)

    expect(result.prompt_text).toBe('')
  })

  it('回复与工具调用字段缺失时给出可渲染的默认值', async () => {
    axiosMock.mockResolvedValueOnce(
      envelope(
        {
          ...detailFixture,
          answer_text: null,
          tool_calls: null,
          refused: undefined,
          warned: undefined
        },
        1
      )
    )

    const result = await getAiPrompt(detailFixture.oid)

    expect(result.answer_text).toBe('')
    expect(result.tool_calls).toBeNull()
    expect(result.refused).toBe(0)
    expect(result.warned).toBe(0)
  })

  it('记录不存在抛出 404 AI_PROMPT_NOT_FOUND', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 404,
      data: { detail: 'AI_PROMPT_NOT_FOUND' },
      headers: {}
    })

    await expect(getAiPrompt('deadbeef')).rejects.toMatchObject({
      name: 'AiRequestError',
      status: 404,
      code: 'AI_PROMPT_NOT_FOUND'
    })
  })
})

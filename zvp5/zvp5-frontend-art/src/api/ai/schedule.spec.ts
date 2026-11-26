import { beforeEach, describe, expect, it, vi } from 'vitest'
import { isValidCronExpression, listAiScheduledTasks } from './schedule'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

const taskFixture = {
  oid: '69b7a4ad0243d973f1e1a2c1',
  owner_oid: '69b7a4ad0243d973f1e1a2b2',
  name: '每日播报',
  prompt_text: '请播报今天的活动',
  review_status: 'pending',
  reviewed_by_oid: null,
  reviewed_at: null,
  review_note: null,
  schedule_kind: 'interval',
  interval_minutes: 60,
  cron_expr: null,
  enabled: true,
  model: null,
  last_run_at: null,
  next_run_at: '2026-10-28T09:00:00',
  run_count: 0,
  last_status: null,
  last_error: null,
  created_at: '2026-10-24T08:00:00',
  updated_at: '2026-10-24T08:00:00'
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

describe('定时任务列表 API 契约', () => {
  it('不带管理参数时 query string 是空的（就是本人任务）', async () => {
    axiosMock.mockResolvedValueOnce(envelope([taskFixture], 1))

    const page = await listAiScheduledTasks()

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'get', url: '/ai/scheduled-tasks', params: {} })
    )
    expect(page.rows).toHaveLength(1)
    expect(page.size).toBe(1)
  })

  it('allTasks 落成 all_tasks=1（后端是 bool 查询参数）', async () => {
    axiosMock.mockResolvedValueOnce(envelope([taskFixture], 1))

    await listAiScheduledTasks({ allTasks: true })

    expect(paramsOf().all_tasks).toBe(1)
  })

  it('allTasks 为假时不写 all_tasks', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAiScheduledTasks({ allTasks: false })

    expect(paramsOf().all_tasks).toBeUndefined()
  })

  it('review_status 与 owner_oid 叠加传，空白值不落到 query string', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiScheduledTasks({ allTasks: true, reviewStatus: 'pending', ownerOid: ' uid-9 ' })
    expect(paramsOf().review_status).toBe('pending')
    expect(paramsOf().owner_oid).toBe('uid-9')

    axiosMock.mockReset()
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiScheduledTasks({ reviewStatus: undefined, ownerOid: '   ' })
    expect(paramsOf().review_status).toBeUndefined()
    expect(paramsOf().owner_oid).toBeUndefined()
  })

  it('limit 与 offset 参与分页；offset 为 0 时不写', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiScheduledTasks({ allTasks: true, limit: 50, offset: 50 })
    expect(paramsOf().limit).toBe(50)
    expect(paramsOf().offset).toBe(50)

    axiosMock.mockReset()
    axiosMock.mockResolvedValueOnce(envelope([], 0))
    await listAiScheduledTasks({ limit: 50, offset: 0 })
    expect(paramsOf().limit).toBe(50)
    expect(paramsOf().offset).toBeUndefined()
  })

  it('size 是当前页条数（不是总数）', async () => {
    axiosMock.mockResolvedValueOnce(envelope([taskFixture], 1))
    const page = await listAiScheduledTasks({ allTasks: true, limit: 50 })
    expect(page.size).toBe(1)
  })
})

describe('cron 表达式本地预检', () => {
  it('接受常见的 5 段式写法', () => {
    expect(isValidCronExpression('0 9 * * *')).toBe(true)
    expect(isValidCronExpression('*/5 * * * *')).toBe(true)
    expect(isValidCronExpression('0 9-18 * * 1-5')).toBe(true)
    expect(isValidCronExpression('0,30 * * * *')).toBe(true)
    expect(isValidCronExpression('0 9 * * mon')).toBe(true)
  })

  it('拒绝段数不对、越界与乱写的表达式', () => {
    expect(isValidCronExpression('')).toBe(false)
    expect(isValidCronExpression('0 9 * *')).toBe(false)
    expect(isValidCronExpression('0 9 * * * *')).toBe(false)
    expect(isValidCronExpression('99 9 * * *')).toBe(false)
    expect(isValidCronExpression('0 25 * * *')).toBe(false)
    expect(isValidCronExpression('0 9 32 * *')).toBe(false)
    expect(isValidCronExpression('0 9 * 13 *')).toBe(false)
    expect(isValidCronExpression('abc def ghi jkl mno')).toBe(false)
  })
})

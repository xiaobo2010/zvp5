import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import UserHomeWorklist from './UserHomeWorklist.vue'

const { pendingMock, recentMock, conversationMock, messagesMock, logsMock, pushMock } = vi.hoisted(
  () => ({
    pendingMock: vi.fn(),
    recentMock: vi.fn(),
    conversationMock: vi.fn(),
    messagesMock: vi.fn(),
    logsMock: vi.fn(),
    pushMock: vi.fn()
  })
)

vi.mock('@/api', () => ({
  default: {
    activity: { read: { pending: pendingMock } },
    ai: {
      listRecentRuns: recentMock,
      getConversation: conversationMock,
      listMessages: messagesMock
    },
    logs: { read: logsMock }
  }
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: pushMock }) }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/plugins/dayjs', () => ({
  default: Object.assign(() => ({ format: () => '2026-10-28' }), {
    unix: () => ({ format: () => '2026-10-28 10:00' })
  })
}))
vi.mock('@element-plus/icons-vue', () => {
  const icon = (name: string) => defineComponent({ name, setup: () => () => h('i') })
  return {
    Refresh: icon('Refresh'),
    WarningFilled: icon('WarningFilled'),
    Clock: icon('Clock'),
    CircleCheck: icon('CircleCheck'),
    CircleClose: icon('CircleClose')
  }
})
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const slotComponent = (name: string) =>
    defineComponent({
      name,
      setup(_, { slots }) {
        return () => h('div', { class: name }, slots.default?.())
      }
    })
  const ElButton = defineComponent({
    name: 'ElButton',
    emits: ['click'],
    setup(_, { slots, emit }) {
      return () => h('button', { onClick: () => emit('click') }, slots.default?.())
    }
  })
  const ElEmpty = defineComponent({
    name: 'ElEmpty',
    setup(_, { slots }) {
      return () => h('div', { class: 'ElEmpty' }, [slots.default?.()])
    }
  })
  const ElPagination = defineComponent({ name: 'ElPagination', template: '<div />' })
  return {
    ElButton,
    ElCard: slotComponent('ElCard'),
    ElEmpty,
    ElIcon: slotComponent('ElIcon'),
    ElPagination,
    ElSkeleton: slotComponent('ElSkeleton'),
    ElTag: slotComponent('ElTag')
  }
})

function makeRun() {
  return {
    oid: 'run-1',
    conversation_oid: 'conversation-1',
    status: 'succeeded' as const,
    model: 'model',
    stop_reason: 'completed' as const,
    rounds: 2,
    error_code: null,
    error_message: null,
    result_message_oid: 'message-1',
    prompt_tokens: 1,
    completion_tokens: 2,
    total_tokens: 3,
    cancel_requested: false,
    created_at: '2026-10-28 10:00:00',
    started_at: null,
    finished_at: null
  }
}

function mountPanel(isAdmin: boolean) {
  return mount(UserHomeWorklist, { props: { isAdmin } })
}

describe('UserHomeWorklist', () => {
  it('shows pending activities and recent agent results to department users', async () => {
    pendingMock.mockResolvedValue({ activities: [], total: 0 })
    recentMock.mockResolvedValue([makeRun()])
    conversationMock.mockResolvedValue({ message_count: 130 })
    messagesMock.mockResolvedValue({
      messages: [{ oid: 'message-1', content: '已处理完成' }]
    })
    logsMock.mockResolvedValue({ data: [], size: 0 })

    const wrapper = mountPanel(false)
    await flushPromises()

    expect(pendingMock).toHaveBeenCalledWith(1, 10)
    expect(recentMock).toHaveBeenCalledWith(5)
    expect(conversationMock).toHaveBeenCalledWith('conversation-1')
    expect(messagesMock).toHaveBeenCalledWith('conversation-1', { afterSeq: 30, limit: 100 })
    expect(wrapper.text()).toContain('已处理完成')
    expect(wrapper.find('.home-worklist__card').exists()).toBe(true)
    expect(logsMock).not.toHaveBeenCalled()
  })

  it('loads suspicious logs only for admins and requests only risk types', async () => {
    pendingMock.mockResolvedValue({ activities: [], total: 0 })
    recentMock.mockResolvedValue([])
    logsMock.mockResolvedValue({
      data: [
        {
          _id: 'log-1',
          url: '/api/risk',
          user: 'u1',
          type: 'risk_alert',
          level: 'warning',
          data: '异常登录',
          timestamp: 1793181600
        }
      ],
      size: 1
    })

    const wrapper = mountPanel(true)
    await flushPromises()

    expect(logsMock).toHaveBeenCalledWith(
      1,
      5,
      '',
      '',
      '',
      expect.objectContaining({ types: expect.arrayContaining(['risk_alert', 'security_alert']) })
    )
    expect(wrapper.text()).toContain('风险告警')
    expect(wrapper.text()).toContain('异常登录')
  })
})

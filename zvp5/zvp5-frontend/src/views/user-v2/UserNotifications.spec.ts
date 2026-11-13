import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import UserNotifications from './UserNotifications.vue'
import { listActive, listRead, markRead } from '@/api/notifications'
import { bindNotificationAccount, notificationRuntime } from '@/components/notification/notificationStore'
import { resetAccountNotificationState } from '@/utils/notificationState'

const { storeState } = vi.hoisted(() => ({
  storeState: {
    _id: 'user-1',
    id: 1
  }
}))

vi.mock('@/api/notifications', () => ({
  listActive: vi.fn(),
  listRead: vi.fn(),
  markRead: vi.fn()
}))

vi.mock('@/stores/user', async () => {
  const { reactive: reactiveImport } = await import('vue')
  return { useUserStore: () => reactiveImport(storeState) }
})

vi.mock('@/stores/header', () => ({ useHeaderStore: () => ({ setHeader: vi.fn() }) }))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

function notice(overrides: Record<string, unknown> = {}) {
  return {
    id: 'notice-1',
    title: '活动调整',
    content: '本周活动改到周六。',
    version: 2,
    author_id: 'author-1',
    author_name: '张老师',
    created_at: '2026-10-21T09:30:00',
    updated_at: '2026-10-21T10:15:00',
    active: true,
    ...overrides
  }
}

const mounted: VueWrapper[] = []

function mountPage(): VueWrapper {
  const wrapper = mount(UserNotifications)
  mounted.push(wrapper)
  return wrapper
}

beforeEach(() => {
  storeState._id = 'user-1'
  storeState.id = 1
  resetAccountNotificationState(storeState._id)
  bindNotificationAccount(storeState._id)
  notificationRuntime.notifications = []
  notificationRuntime.notificationsLoaded = false
  notificationRuntime.failed = false
  notificationRuntime.readFailed = false
  vi.mocked(listActive).mockReset().mockResolvedValue({ version: 7, notifications: [] })
  vi.mocked(listRead).mockReset().mockResolvedValue({})
  vi.mocked(markRead).mockReset().mockResolvedValue({ oid: 'notice-1', version: 2 })
})

afterEach(() => {
  while (mounted.length > 0) {
    mounted.pop()?.unmount()
  }
})

describe('UserNotifications 收件箱', () => {
  it('列出全部现役通知，已读与未读都可见', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [
        notice({ id: 'notice-unread', title: '未读通知' }),
        notice({ id: 'notice-read', title: '已读通知' })
      ]
    })
    vi.mocked(listRead).mockResolvedValue({ 'notice-read': 2 })

    const wrapper = mountPage()
    await flushPromises()

    expect(listActive).toHaveBeenCalledTimes(1)
    // 已读映射与通知列表并行拉取。
    expect(listRead).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('未读通知')
    expect(wrapper.text()).toContain('已读通知')
    // 未读用强调色六边形角标标出。
    expect(wrapper.findAll('.notif-item__badge.is-unread')).toHaveLength(1)
  })

  it('展开后可以标记已读，写穿服务端', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.notif-item__head').trigger('click')
    await flushPromises()
    expect(wrapper.find('.notif-item__body').exists()).toBe(true)

    await wrapper.find('.notif-btn--primary').trigger('click')
    await flushPromises()

    expect(markRead).toHaveBeenCalledWith('notice-1')
    // 标记后这条通知不再算未读。
    expect(wrapper.find('.notif-item__badge.is-unread').exists()).toBe(false)
  })

  it('「不再显示」只落本地，不写服务端', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.notif-item__head').trigger('click')
    await flushPromises()
    const buttons = wrapper.findAll('.notif-item__actions .notif-btn')
    await buttons[1].trigger('click')
    await flushPromises()

    expect(markRead).not.toHaveBeenCalled()
    expect(wrapper.find('.notif-item__badge.is-unread').exists()).toBe(false)
  })

  it('空态有明确文案', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.notif-empty').exists()).toBe(true)
    expect(wrapper.text()).toContain('notificationCenter.page.empty')
  })

  it('加载失败时给出重试入口', async () => {
    vi.mocked(listActive).mockRejectedValue(new Error('network'))
    vi.mocked(listRead).mockRejectedValue(new Error('network'))

    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.notif-empty').exists()).toBe(true)
    expect(wrapper.find('.notif-empty__title').text()).toBe('notificationCenter.page.failed')
    expect(wrapper.find('.notif-empty__actions .notif-btn').exists()).toBe(true)
  })
})

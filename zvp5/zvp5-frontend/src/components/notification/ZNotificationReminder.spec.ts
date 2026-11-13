import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import ZNotificationReminder from './ZNotificationReminder.vue'
import { listActive, listRead, markRead } from '@/api/notifications'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { bindNotificationAccount, notificationRuntime } from './notificationStore'
import { resetAccountNotificationState } from '@/utils/notificationState'

const { storeState, routeState, pushSpy } = vi.hoisted(() => ({
  storeState: {
    isLogin: true,
    _id: 'user-1',
    id: 1
  },
  routeState: {
    path: '/'
  },
  pushSpy: vi.fn()
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

vi.mock('vue-router', async () => {
  const { reactive: reactiveImport } = await import('vue')
  const route = reactiveImport(routeState)
  return {
    useRoute: () => route,
    useRouter: () => ({ push: pushSpy })
  }
})

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

function mountReminder(): VueWrapper {
  bindNotificationAccount(storeState._id)
  return mount(ZNotificationReminder)
}

/**
 * 每个用例结束都要卸载组件：横幅里挂着定时器与全局监听，
 * 残留的实例会在后续用例里继续发请求，让断言看起来「凭空多出一次调用」。
 */
const mounted: VueWrapper[] = []

function mountTracked(): VueWrapper {
  const wrapper = mountReminder()
  mounted.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date']
  })
  storeState.isLogin = true
  storeState._id = 'user-1'
  storeState.id = 1
  routeState.path = '/'
  localStorage.clear()
  resetAccountNotificationState(storeState._id)
  notificationRuntime.notifications = []
  notificationRuntime.failed = false
  notificationRuntime.readFailed = false
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: 'visible'
  })
  vi.mocked(listActive).mockReset()
  vi.mocked(listRead).mockReset()
  vi.mocked(markRead).mockReset()
  vi.mocked(listActive).mockResolvedValue({ version: 7, notifications: [] })
  vi.mocked(listRead).mockResolvedValue({})
  vi.mocked(markRead).mockResolvedValue({ oid: 'notice-1', version: 2 })
  pushSpy.mockReset()
})

afterEach(() => {
  while (mounted.length > 0) {
    mounted.pop()?.unmount()
  }
  vi.useRealTimers()
})

describe('ZNotificationReminder 初始延迟', () => {
  it('登录后 9.9s 不请求，10s 才请求一次', async () => {
    const wrapper = mountTracked()

    // 登录即取一次已读映射：体积小，且所有入口都要靠它判断未读。
    expect(listRead).toHaveBeenCalledTimes(1)
    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(9_900)
    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(listActive).toHaveBeenCalledTimes(1)
    // 10s 这一轮里已读映射与通知列表并行拉取。
    expect(listRead).toHaveBeenCalledTimes(2)

    // 60s 节流仍然生效，不产生额外轮询。
    await vi.advanceTimersByTimeAsync(60_000)
    await flushPromises()
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('10s 内路由变化与可见性变化不能绕过延迟', async () => {
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(5_000)
    useRoute().path = '/other'
    await nextTick()
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(4_900)
    await flushPromises()

    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('切号后旧账号的定时器不再触发', async () => {
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(5_000)
    useUserStore()._id = 'user-2'
    await nextTick()

    // 旧 timer 已清理；新账号重新计满 10s。
    await vi.advanceTimersByTimeAsync(9_999)
    await flushPromises()
    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('卸载后旧定时器不再触发', async () => {
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(5_000)
    wrapper.unmount()

    await vi.advanceTimersByTimeAsync(60_000)
    await flushPromises()
    expect(listActive).not.toHaveBeenCalled()
  })

  it('登出时清定时器且不请求', async () => {
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(5_000)
    useUserStore().isLogin = false
    await nextTick()

    await vi.advanceTimersByTimeAsync(60_000)
    await flushPromises()
    expect(listActive).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

describe('ZNotificationReminder 顶部消息框', () => {
  it('未读通知以非模态横幅出现在页面顶部', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    const banner = wrapper.find('.zn-banner')
    expect(banner.exists()).toBe(true)
    // 非模态：没有遮罩，也没有对话框角色。
    expect(wrapper.find('.el-overlay').exists()).toBe(false)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(banner.attributes('role')).toBe('region')
    expect(wrapper.text()).toContain('活动调整')

    wrapper.unmount()
  })

  it('服务端已读的通知不再弹出', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    vi.mocked(listRead).mockResolvedValue({ 'notice-1': 2 })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    expect(wrapper.find('.zn-banner').exists()).toBe(false)

    wrapper.unmount()
  })

  it('「已读」写穿服务端，成功后这条通知不再出现在横幅上', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    expect(wrapper.find('.zn-banner').exists()).toBe(true)

    await wrapper.find('.zn-banner__btn--primary').trigger('click')
    await flushPromises()

    expect(markRead).toHaveBeenCalledWith('notice-1')
    expect(wrapper.find('.zn-banner').exists()).toBe(false)

    wrapper.unmount()
  })

  it('「已读」写失败时保留横幅并给出重试入口', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    vi.mocked(markRead).mockRejectedValue(new Error('network'))
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    await wrapper.find('.zn-banner__btn--primary').trigger('click')
    await flushPromises()

    // 写失败 → 回滚，横幅仍在，并出现重试入口。
    expect(wrapper.find('.zn-banner').exists()).toBe(true)
    expect(wrapper.find('.zn-banner__error').exists()).toBe(true)
    expect(wrapper.find('.zn-banner__retry').exists()).toBe(true)

    wrapper.unmount()
  })

  it('「不再显示」只落本地，不写服务端', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    await wrapper.findAll('.zn-banner__btn')[0].trigger('click')
    await flushPromises()

    expect(markRead).not.toHaveBeenCalled()
    expect(wrapper.find('.zn-banner').exists()).toBe(false)

    wrapper.unmount()
  })

  it('顶部横幅提供「查看全部通知」入口', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    await wrapper.find('.zn-banner__link').trigger('click')
    expect(pushSpy).toHaveBeenCalledWith('/notifications')

    wrapper.unmount()
  })

  it('Esc 收起横幅，通知留待下次检查再提醒', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 7,
      notifications: [notice()]
    })
    const wrapper = mountTracked()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    expect(wrapper.find('.zn-banner').exists()).toBe(true)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.zn-banner').exists()).toBe(false)
    // 收起不等于已读：服务端没有收到任何写穿请求。
    expect(markRead).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

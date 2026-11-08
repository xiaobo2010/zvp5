import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import ZNotificationReminder from './ZNotificationReminder.vue'
import { listActive } from '@/api/notifications'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'

const { storeState, routeState } = vi.hoisted(() => ({
  storeState: {
    isLogin: true,
    _id: 'user-1',
    id: 1
  },
  routeState: {
    path: '/'
  }
}))

vi.mock('@/api/notifications', () => ({
  listActive: vi.fn()
}))

vi.mock('@/stores/user', async () => {
  const { reactive: reactiveImport } = await import('vue')
  return { useUserStore: () => reactiveImport(storeState) }
})

vi.mock('vue-router', async () => {
  const { reactive: reactiveImport } = await import('vue')
  const route = reactiveImport(routeState)
  return { useRoute: () => route }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [slots.default?.(), slots.footer?.()])
          : null
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { disabled: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            onClick: () => {
              if (!props.disabled) {
                emit('click')
              }
            }
          },
          slots.default?.()
        )
    }
  })

  return { ElDialog, ElButton }
})

function mountReminder(): VueWrapper {
  return mount(ZNotificationReminder)
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
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: 'visible'
  })
  vi.mocked(listActive).mockReset()
  vi.mocked(listActive).mockResolvedValue({ version: 0, notifications: [] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ZNotificationReminder 初始延迟', () => {
  it('登录后 9.9s 不请求，10s 才请求一次', async () => {
    const wrapper = mountReminder()

    await vi.advanceTimersByTimeAsync(9_900)
    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(100)
    expect(listActive).toHaveBeenCalledTimes(1)

    // 60s 节流仍然生效，不产生额外轮询。
    await vi.advanceTimersByTimeAsync(60_000)
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('10s 内路由变化与可见性变化不能绕过延迟', async () => {
    const wrapper = mountReminder()

    await vi.advanceTimersByTimeAsync(5_000)
    useRoute().path = '/other'
    await nextTick()
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(4_900)

    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(100)
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('切号后旧账号的定时器不再触发', async () => {
    const wrapper = mountReminder()

    await vi.advanceTimersByTimeAsync(5_000)
    useUserStore()._id = 'user-2'
    await nextTick()

    // 旧 timer 已清理；新账号重新计满 10s。
    await vi.advanceTimersByTimeAsync(9_999)
    expect(listActive).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    expect(listActive).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('卸载后旧定时器不再触发', async () => {
    const wrapper = mountReminder()

    await vi.advanceTimersByTimeAsync(5_000)
    wrapper.unmount()

    await vi.advanceTimersByTimeAsync(60_000)
    expect(listActive).not.toHaveBeenCalled()
  })

  it('登出时清定时器且不请求', async () => {
    const wrapper = mountReminder()

    await vi.advanceTimersByTimeAsync(5_000)
    useUserStore().isLogin = false
    await nextTick()

    await vi.advanceTimersByTimeAsync(60_000)
    expect(listActive).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

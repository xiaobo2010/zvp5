import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserNav from './UserNav.vue'
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'

const { storeState, replaceSpy, pushSpy, confirmSpy } = vi.hoisted(() => ({
  storeState: {
    id: 42 as number | string,
    _id: 'user-1',
    name: '张三',
    position: [] as string[],
    class_id: '',
    resetPassword: vi.fn(async () => {}),
    removeUser: vi.fn(async () => {})
  },
  replaceSpy: vi.fn(),
  pushSpy: vi.fn(),
  confirmSpy: vi.fn()
}))

vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  return { useUserStore: () => reactive(storeState) }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushSpy,
    replace: replaceSpy,
    resolve: (path: string) => ({ path, matched: [] })
  }),
  useRoute: () => ({ path: '/user' })
}))

vi.mock('vue-i18n', async () => {
  const { ref } = await import('vue')
  return {
    useI18n: () => ({
      t: (key: string) => key,
      locale: ref('zh-CN')
    })
  }
})

vi.mock('@/utils/routePrefetch', () => ({
  prefetchRoute: vi.fn(),
  prefetchRoutesWhenIdle: vi.fn()
}))

vi.mock('@/views/user/modifyPasswordDialogs', () => ({
  modifyPasswordDialogs: vi.fn(async () => {})
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElTooltip = defineComponent({
    name: 'ElTooltip',
    props: { content: String, placement: String, effect: String, showAfter: Number },
    setup(props, { slots }) {
      return () => h('span', { class: 'el-tooltip-stub' }, slots.default?.())
    }
  })

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(props, { slots }) {
      return () => h('span', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  /* 真实 ElPopover 由点击 reference 切换显隐，这里用同样的语义做桩 */
  const ElPopover = defineComponent({
    name: 'ElPopover',
    props: {
      visible: Boolean,
      placement: String,
      trigger: String,
      width: String,
      popperClass: String,
      popperOptions: Object
    },
    emits: ['update:visible'],
    setup(props, { slots, emit }) {
      return () =>
        h('div', { class: 'el-popover-stub' }, [
          h(
            'span',
            {
              class: 'el-popover-stub__reference',
              onClick: () => emit('update:visible', !props.visible)
            },
            slots.reference?.()
          ),
          props.visible
            ? h('div', { class: 'el-popover-stub__body' }, slots.default?.())
            : null
        ])
    }
  })

  return {
    ElTooltip,
    ElIcon,
    ElPopover,
    ElMessageBox: { confirm: confirmSpy }
  }
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as never)

async function mountNav(): Promise<VueWrapper> {
  const wrapper = mount(UserNav)
  await flushPromises()
  return wrapper
}

async function openAccountMenu(wrapper: VueWrapper) {
  await wrapper.find('.yg-glass-nav__account').trigger('click')
  await flushPromises()
  return wrapper.findAll('.yg-glass-account-menu__item')
}

beforeEach(() => {
  storeState.id = 42
  storeState.name = '张三'
  storeState.position = []
  storeState.resetPassword.mockClear()
  storeState.removeUser.mockClear()
  replaceSpy.mockClear()
  pushSpy.mockClear()
  confirmSpy.mockReset()
  vi.mocked(modifyPasswordDialogs).mockClear()
})

describe('UserNav 账号菜单', () => {
  it('导航末尾提供账号入口，带无障碍标签与头像首字', async () => {
    const wrapper = await mountNav()

    const account = wrapper.find('.yg-glass-nav__account')
    expect(account.exists()).toBe(true)
    expect(account.attributes('aria-label')).toBe('nav.accountMenu')
    expect(account.attributes('aria-haspopup')).toBe('menu')
    expect(wrapper.find('.yg-glass-nav__avatar').text()).toBe('张')

    wrapper.unmount()
  })

  it('展开后提供密码修改与退出登录两项', async () => {
    const wrapper = await mountNav()

    const items = await openAccountMenu(wrapper)
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('nav.reset')
    expect(items[1].text()).toContain('nav.logout')

    wrapper.unmount()
  })

  it('密码修改走改密弹窗，取消时静默', async () => {
    const wrapper = await mountNav()
    const items = await openAccountMenu(wrapper)

    vi.mocked(modifyPasswordDialogs).mockRejectedValueOnce(new Error('cancel'))
    await items[0].trigger('click')
    await flushPromises()

    expect(modifyPasswordDialogs).toHaveBeenCalledTimes(1)
    expect(modifyPasswordDialogs).toHaveBeenCalledWith('42', 'zh-CN', storeState.resetPassword)
    expect(storeState.removeUser).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('退出登录二次确认通过后清会话并回到登录页', async () => {
    const wrapper = await mountNav()
    const items = await openAccountMenu(wrapper)

    confirmSpy.mockResolvedValueOnce('confirm')
    await items[1].trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(storeState.removeUser).toHaveBeenCalledTimes(1)
    expect(replaceSpy).toHaveBeenCalledWith('/user/login')

    wrapper.unmount()
  })

  it('退出登录二次确认取消时不退出', async () => {
    const wrapper = await mountNav()
    const items = await openAccountMenu(wrapper)

    confirmSpy.mockRejectedValueOnce(new Error('cancel'))
    await items[1].trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(storeState.removeUser).not.toHaveBeenCalled()
    expect(replaceSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

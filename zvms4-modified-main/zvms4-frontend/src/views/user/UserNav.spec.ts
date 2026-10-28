import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h, ref, type SetupContext } from 'vue'
import UserNav from './UserNav.vue'

/* 主题切换的旋转态必须在任何动效策略下都能解锁：
   - 减少动效时 base.css 把 animation-duration 压到 0.01ms，animationend 不再保证派发；
   - jsdom 根本不派发 animationend，相当于「事件被吞」的极端环境。
   两条用例分别覆盖「减少动效直接换肤」与「令牌兜底解锁」，再加一条 ViewTransition
   结束解锁，防止 spinning 把主题开关永久锁死。 */

const darkRef = ref(false)

vi.mock('element-plus', async () => {
  const { defineComponent } = await import('vue')
  return {
    ElIcon: defineComponent({
      name: 'ElIcon',
      setup(_props: unknown, { slots }: SetupContext) {
        return () => h('span', { class: 'el-icon' }, slots.default?.())
      }
    }),
    ElTooltip: defineComponent({
      name: 'ElTooltip',
      setup(_props: unknown, { slots }: SetupContext) {
        return () => h('span', { class: 'el-tooltip-stub' }, slots.default?.())
      }
    })
  }
})

vi.mock('@element-plus/icons-vue', () => {
  const icon = { name: 'IconStub', template: '<i />' }
  return {
    Avatar: icon,
    HomeFilled: icon,
    Sunny: icon,
    Moon: icon,
    Management: icon,
    Monitor: icon,
    ChatLineRound: icon,
    EditPen: icon
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  /* 登记子路由：上一版 judge 用 path === '/activity/register' 时这里整条导航无高亮 */
  useRoute: () => ({ path: '/activity/register/create' })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string, fallback?: string) => fallback ?? key })
}))

vi.mock('@vueuse/core', () => ({
  useDark: () => darkRef,
  useWindowSize: () => ({ width: ref(1200), height: ref(800) })
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ position: ['admin'], class_id: 'class-1' })
}))

vi.mock('@/plugins/ua', () => ({
  pad: () => false,
  getTabletType: () => 'unknown'
}))

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: matches && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  }))
}

function mountNav(): VueWrapper {
  return mount(UserNav)
}

beforeEach(() => {
  darkRef.value = false
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'startViewTransition')
  darkRef.value = false
})

it('高亮登记子路由（选择器/编辑页）并保持单一高亮', () => {
  stubReducedMotion(false)
  const wrapper = mountNav()
  const active = wrapper.findAll('.yg-glass-nav__item.is-active')

  expect(active).toHaveLength(1)
  expect(active[0].attributes('aria-label')).toBe('nav.register')
  expect(wrapper.findAll('[aria-label="nav.register"]')).toHaveLength(1)
  expect(wrapper.findAll('[aria-label="nav.create"]')).toHaveLength(0)
  wrapper.unmount()
})

it('减少动效下不挂旋转态，连续两次点击仍能切换主题', async () => {
  stubReducedMotion(true)
  const wrapper = mountNav()
  const theme = wrapper.get('.yg-glass-nav__theme')

  await theme.trigger('click')
  expect(darkRef.value).toBe(true)
  expect(wrapper.get('.yg-glass-nav__theme-icon').classes()).not.toContain('spinning')

  /* 关键回归：旧实现此时 spinning 永久为 true，第二次点击被守卫吞掉 */
  await theme.trigger('click')
  expect(darkRef.value).toBe(false)
  wrapper.unmount()
})

it('没有 animationend 时按 --yg-dur-page 令牌兜底解锁', async () => {
  stubReducedMotion(false)
  const tokenStyle = {
    getPropertyValue: (name: string) => (name === '--yg-dur-page' ? '450ms' : '')
  } as unknown as CSSStyleDeclaration
  vi.spyOn(window, 'getComputedStyle').mockReturnValue(tokenStyle)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

  const wrapper = mountNav()
  const theme = wrapper.get('.yg-glass-nav__theme')

  await theme.trigger('click')
  expect(darkRef.value).toBe(true)

  /* 旋转期内连点仍按原语义忽略 */
  await theme.trigger('click')
  expect(darkRef.value).toBe(true)

  /* 令牌时长未到不解锁 */
  vi.advanceTimersByTime(449)
  await theme.trigger('click')
  expect(darkRef.value).toBe(true)

  /* 兜底定时到点后恢复可用 */
  vi.advanceTimersByTime(1)
  await theme.trigger('click')
  expect(darkRef.value).toBe(false)
  wrapper.unmount()
})

it('ViewTransition.finished 也会解锁（与 animationend 互补）', async () => {
  stubReducedMotion(false)
  const startViewTransition = vi.fn((update: () => void) => {
    update()
    return { finished: Promise.resolve() }
  })
  Object.defineProperty(document, 'startViewTransition', {
    configurable: true,
    value: startViewTransition
  })

  const wrapper = mountNav()
  const theme = wrapper.get('.yg-glass-nav__theme')

  await theme.trigger('click')
  expect(darkRef.value).toBe(true)
  await flushPromises()
  expect(wrapper.get('.yg-glass-nav__theme-icon').classes()).not.toContain('spinning')

  await theme.trigger('click')
  expect(darkRef.value).toBe(false)
  wrapper.unmount()
})

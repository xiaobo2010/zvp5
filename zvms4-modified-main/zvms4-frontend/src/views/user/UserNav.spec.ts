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
/* 路由路径必须可变且是真正的 ref：「切到我的」的弹簧动画回归用例要在 /user ↔ /me
   之间往返，组件内部 watch 依赖 route.path；静态字符串会让第二次切换根本不触发重算。
   用 holder 中转是因为 vi.mock 的工厂会被提升到 import 之前，不能直接引用模块常量。 */
const routeHolder = vi.hoisted(() => ({ ref: null as null | { value: string } }))

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

vi.mock('vue-router', async () => {
  const { ref } = await import('vue')
  routeHolder.ref = ref('/activity/register/create')
  return {
    useRouter: () => ({ push: vi.fn() }),
    /* 登记子路由：上一版 judge 用 path === '/activity/register' 时这里整条导航无高亮。
       getter 让 computed/watch 能追踪路径 ref 的变化。 */
    useRoute: () => ({
      get path() {
        return routeHolder.ref?.value ?? '/activity/register/create'
      }
    })
  }
})

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
  if (routeHolder.ref) routeHolder.ref.value = '/activity/register/create'
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
  Reflect.deleteProperty(Element.prototype, 'animate')
  darkRef.value = false
})

/* jsdom 没有布局：offset* 全是 0，指示器会把自己判成「隐藏项」。这里按 DOM 顺序
   给每个导航项铺一份假几何，让 updateIndicator 真的算出位移。 */
function stubItemGeometry(wrapper: VueWrapper) {
  wrapper.findAll('.yg-glass-nav__item').forEach((item, index) => {
    const el = item.element as HTMLElement
    Object.defineProperty(el, 'offsetLeft', { configurable: true, value: index * 46 })
    Object.defineProperty(el, 'offsetTop', { configurable: true, value: 8 })
    Object.defineProperty(el, 'offsetWidth', { configurable: true, value: 44 })
    Object.defineProperty(el, 'offsetHeight', { configurable: true, value: 44 })
  })
}

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

it('每次切到「我的」都播放弹簧动画，重复进入也不例外', async () => {
  /* 回归：CSS transition 版本在导航到 /me 时会被页面挂载的样式重算取消
     （transitioncancel），表现为第一次有动画、之后再点直接跳过去。
     指示器改为显式 WAAPI 弹簧后，每一次高亮变化都必须新建动画。 */
  stubReducedMotion(false)
  const animate = vi.fn(() => ({ finished: Promise.resolve(), cancel: vi.fn() }))
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    value: animate
  })
  vi.spyOn(window, 'getComputedStyle').mockImplementation(((el: Element) => {
    const style = (el as HTMLElement).style
    return {
      transform: style?.transform || 'none',
      getPropertyValue: (name: string) =>
        name === '--yg-dur-page'
          ? '450ms'
          : name === '--yg-ease-spring'
            ? 'cubic-bezier(0.34, 1.56, 0.64, 1)'
            : ''
    } as unknown as CSSStyleDeclaration
  }) as typeof window.getComputedStyle)

  const wrapper = mountNav()
  stubItemGeometry(wrapper)
  await flushPromises()

  for (let round = 0; round < 3; round += 1) {
    routeHolder.ref!.value = '/user'
    await flushPromises()
    routeHolder.ref!.value = '/me'
    await flushPromises()
  }

  /* 每轮往返各一次（首页、我的），共 6 次；这里只盯「切到我的」那 3 次：
     起点必须是上一项（首页 = x 0），终点是「我的」（x 184），
     且重复进入也照样播放，不能退化成瞬移。 */
  const toProfile = (animate.mock.calls as unknown as [frames: { transform: string }[]][])
    .map((call) => call[0])
    .filter((frames) => frames[1]?.transform === 'translate3d(184px, 8px, 0)')
  expect(toProfile).toHaveLength(3)
  for (const frames of toProfile) {
    // 起点是上一项（首页），终点是「我的」：有真实位移才可能有弹簧
    expect(frames[0].transform).toBe('translate3d(0px, 8px, 0)')
  }

  wrapper.unmount()
})

it('减少动效时切页直接落位、不新建动画', async () => {
  stubReducedMotion(true)
  const animate = vi.fn()
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    value: animate
  })

  const wrapper = mountNav()
  stubItemGeometry(wrapper)
  await flushPromises()

  routeHolder.ref!.value = '/me'
  await flushPromises()

  expect(animate).not.toHaveBeenCalled()
  wrapper.unmount()
})

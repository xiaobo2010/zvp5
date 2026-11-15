import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import ServerHome from './ServerHome.vue'

const routerMock = vi.hoisted(() => ({
  replace: vi.fn(),
  back: vi.fn()
}))

const routeState = vi.hoisted(() => ({ path: '/server/maintenance' }))

const userState = vi.hoisted(() => ({ position: ['admin'] as string[] }))

// 直接用带 value 的普通对象：mock 工厂在首次导入时才跑，beforeEach 里必须能安全赋值。
const windowSize = vi.hoisted(() => ({
  width: { value: 400 } as { value: number },
  height: { value: 900 } as { value: number }
}))

vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive(routeState)
  return {
    useRoute: () => route,
    useRouter: () => routerMock,
    RouterView: { name: 'RouterView', template: '<div class="router-view-stub" />' }
  }
})

vi.mock('@vueuse/core', async () => {
  return {
    useWindowSize: () => ({ width: windowSize.width, height: windowSize.height })
  }
})

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => userState
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

const pageHeaderStub = {
  name: 'ElPageHeader',
  props: { icon: Object },
  emits: ['back'],
  template: '<header class="el-page-header-stub"><slot name="content" /><slot name="extra" /></header>'
}

const segmentedStub = {
  name: 'ElSegmented',
  props: { modelValue: String, options: Array },
  emits: ['update:modelValue'],
  template:
    '<div class="el-segmented-stub" @click="$emit(\'update:modelValue\', options && options[0].value)"><slot :item="options && options[0]" /></div>'
}

function mountPage() {
  return mount(ServerHome, {
    global: {
      stubs: {
        ElPageHeader: pageHeaderStub,
        ElSegmented: segmentedStub,
        ElIcon: { template: '<span><slot /></span>' }
      }
    }
  })
}

function tabButtons(wrapper: ReturnType<typeof mountPage>) {
  return wrapper.findAll('[role="tab"]')
}

beforeEach(() => {
  routerMock.replace.mockReset()
  routerMock.back.mockReset()
  routeState.path = '/server/maintenance'
  userState.position = ['admin']
  windowSize.width.value = 400
  windowSize.height.value = 900
})

it('redirects non-admins to /not-found', async () => {
  userState.position = ['user']

  const wrapper = mountPage()
  await flushPromises()

  expect(routerMock.replace).toHaveBeenCalledWith('/not-found')

  wrapper.unmount()
})

it('replaces the route when a tablet tab is picked', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const dbContentTab = tabButtons(wrapper).find((tab) => tab.text() === 'server.tabs.dbContent')
  expect(dbContentTab).toBeTruthy()

  await dbContentTab!.trigger('click')
  await flushPromises()

  expect(routerMock.replace).toHaveBeenCalledWith('/server/db/content')

  wrapper.unmount()
})

it('falls back to maintenance for an unknown server path', async () => {
  routeState.path = '/server/bogus'

  const wrapper = mountPage()
  await flushPromises()

  expect(routerMock.replace).toHaveBeenCalledWith('/server/maintenance')
  expect(wrapper.text()).toContain('server.tabs.maintenance')

  wrapper.unmount()
})

it('renders the pill navigation with the active tab marked in compact mode', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(wrapper.find('nav.server-tabs').exists()).toBe(true)

  const tabs = tabButtons(wrapper)
  expect(tabs).toHaveLength(6)

  const active = tabs.filter((tab) => tab.attributes('aria-selected') === 'true')
  expect(active).toHaveLength(1)
  expect(active[0]!.text()).toBe('server.tabs.maintenance')

  wrapper.unmount()
})

it('switches back to the segmented control on wide screens', async () => {
  windowSize.width.value = 1440
  windowSize.height.value = 900

  const wrapper = mountPage()
  await flushPromises()
  await nextTick()

  expect(wrapper.find('nav.server-tabs').exists()).toBe(false)
  // 宽屏用分支自己的分段控件 + 页头（不是 main 的 ElPageHeader）
  expect(wrapper.find('.yg2-page-header').exists()).toBe(true)

  wrapper.unmount()
})

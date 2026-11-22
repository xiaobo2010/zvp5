import { defineComponent, h, type Component } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CanaryShell from './canary/CanaryShell.vue'
import Canary2Shell from './canary2/Canary2Shell.vue'
import CanaryProShell from './canary-pro/CanaryProShell.vue'

/**
 * 外壳级断言：canary 与 canary-pro 必须是两套结构，而不是同一 DOM 换色。
 * - canary：.yg-layout + UserNav（Phi 外壳）
 * - canary-pro：.pro-app + .pro-rail + .pro-topbar（工作台外壳）
 */

const routeState = { path: '/user' }

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() })
}))

vi.mock('@vueuse/core', async () => {
  const { ref: makeRef } = await import('vue')
  return {
    useWindowSize: () => ({ width: makeRef(1280), height: makeRef(800) })
  }
})

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    isLogin: true,
    id: 1,
    name: '张三',
    position: ['admin'],
    class_id: 'c1',
    resetPassword: false,
    removeUser: vi.fn()
  })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key })
}))

vi.mock('@/stores/header', () => ({
  useHeaderStore: () => ({ header: '首页 - 义工平台', base: '义工平台', resetHeader: vi.fn() })
}))

vi.mock('@/views/user-v2/UserNav.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'UserNav',
      setup: () => () => h('nav', { class: 'user-nav-stub' })
    })
  }
})

vi.mock('@/components/decor/ZHexGrid.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZHexGrid',
      setup: () => () => h('div', { class: 'hex-grid-stub' })
    })
  }
})

vi.mock('@/utils/phiEdgePhase', () => ({
  startPhiEdgePhase: vi.fn(),
  stopPhiEdgePhase: vi.fn()
}))

vi.mock('@/views/user/modifyPasswordDialogs', () => ({
  modifyPasswordDialogs: vi.fn()
}))

const Content = defineComponent({
  setup: () => () => h('div', { class: 'page-content-stub' }, 'content')
})

function mountWithContent(component: Component) {
  return mount(component, {
    slots: { default: () => h(Content) },
    global: {
      stubs: {
        Transition: false
      }
    }
  })
}

beforeEach(() => {
  routeState.path = '/user'
})

describe('主题外壳结构', () => {
  it('canary 渲染 Phi 外壳与 UserNav', () => {
    const wrapper = mountWithContent(CanaryShell)

    expect(wrapper.find('.yg-layout--app').exists()).toBe(true)
    expect(wrapper.find('.user-nav-stub').exists()).toBe(true)
    expect(wrapper.find('.z-hex-grid, .hex-grid-stub').exists()).toBe(true)
    expect(wrapper.find('.pro-app').exists()).toBe(false)
  })

  it('canary-pro 渲染工作台外壳，不出现 Phi 的 UserNav/六边形', () => {
    const wrapper = mountWithContent(CanaryProShell)

    expect(wrapper.find('.pro-app').exists()).toBe(true)
    expect(wrapper.find('.pro-rail').exists()).toBe(true)
    expect(wrapper.find('.pro-topbar').exists()).toBe(true)
    expect(wrapper.find('.pro-workspace').exists()).toBe(true)
    expect(wrapper.find('.user-nav-stub').exists()).toBe(false)
    expect(wrapper.find('.yg-layout').exists()).toBe(false)
    expect(wrapper.find('.hex-grid-stub').exists()).toBe(false)
  })

  it('canary2 渲染光场 + 分组侧栏 + 顶栏', () => {
    const wrapper = mountWithContent(Canary2Shell)

    expect(wrapper.find('.yg2-app').exists()).toBe(true)
    expect(wrapper.find('.yg2-ambient').exists()).toBe(true)
    expect(wrapper.find('.yg2-sidebar').exists()).toBe(true)
    expect(wrapper.find('.yg2-topbar').exists()).toBe(true)
    expect(wrapper.find('.user-nav-stub').exists()).toBe(false)
  })

  it('两套外壳根节点与导航结构不同', () => {
    const canary = mountWithContent(CanaryShell)
    const pro = mountWithContent(CanaryProShell)

    expect(canary.element.className).toContain('yg-layout')
    expect(pro.element.className).toContain('pro-app')
    expect(canary.html()).not.toBe(pro.html())
  })
})

import { reactive, nextTick } from 'vue'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import UserHome from './UserHome.vue'

const mocks = vi.hoisted(() => ({
  state: {
    isLogin: true,
    _id: 'account-a',
    id: 2026001,
    name: '同学',
    groups: [] as string[],
    position: ['student'],
    setTime: vi.fn()
  },
  read: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  setHeader: vi.fn()
}))
vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  return { useUserStore: () => reactive(mocks.state) }
})
vi.mock('@/stores/header', () => ({ useHeaderStore: () => ({ setHeader: mocks.setHeader }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push, replace: mocks.replace }) }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/api', () => ({ default: { user: { time: { read: mocks.read } } } }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    ElDialog: defineComponent({
      name: 'ElDialog',
      props: ['modelValue'],
      setup:
        (props, { slots }) =>
        () =>
          props.modelValue ? h('div', slots.default?.()) : null
    })
  }
})
vi.mock('@/components/home/HomeOverview.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'HomeOverview',
      props: ['name', 'number', 'userKey', 'progress', 'loading', 'error'],
      emits: ['refresh', 'details', 'profile', 'activities'],
      setup:
        (_, { slots }) =>
        () =>
          h('section', [slots.identity?.(), slots.footer?.()])
    })
  }
})
vi.mock('@/components/activity/ZDetailedAnalysis.vue', () => ({
  __esModule: true,
  default: { name: 'ZDetailedAnalysis', props: ['id', 'mode', 'sums'], template: '<div>明细</div>' }
}))
vi.mock('@/components/user/UserHomeWorklist.vue', () => ({
  default: { name: 'UserHomeWorklist', props: ['isAdmin'], template: '<div>待办</div>' }
}))
vi.mock('@/components/tags/ZUserGroup.vue', () => ({ default: { template: '<span />' } }))
vi.mock('@/components/tags/ZUserPosition.vue', () => ({ default: { template: '<span />' } }))

const store = reactive(mocks.state)
const sums = { 'on-campus': 12.5, 'off-campus': 6, 'social-practice': 9 }
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.clearAllMocks()
  Object.assign(store, {
    isLogin: true,
    _id: 'account-a',
    id: 2026001,
    name: '同学',
    groups: [],
    position: ['student']
  })
  mocks.read.mockReset().mockResolvedValue(sums)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

describe('UserHome integration', () => {
  it('keeps progress unknown until the current account response arrives, then shares its sums with details', async () => {
    let resolve!: (value: typeof sums) => void
    mocks.read.mockReturnValueOnce(
      new Promise<typeof sums>((done) => {
        resolve = done
      })
    )
    wrapper = mount(UserHome)
    const overview = wrapper.getComponent({ name: 'HomeOverview' })
    expect(overview.props('progress')).toBeNull()
    expect(overview.props('loading')).toBe(true)
    expect(mocks.read).toHaveBeenCalledWith('account-a')
    resolve(sums)
    await flushPromises()
    expect(
      overview.props('progress').categories.map((c: { percent: number }) => c.percent)
    ).toEqual([50, 40, 50])
    expect(mocks.state.setTime).toHaveBeenCalledWith(sums)
    overview.vm.$emit('details')
    await flushPromises()
    const details = wrapper.getComponent({ name: 'ZDetailedAnalysis' })
    expect(details.props('id')).toBe('account-a')
    expect(details.props('sums')).toEqual(sums)
    store._id = 'account-b'
    await flushPromises()
    expect(wrapper.findComponent({ name: 'ZDetailedAnalysis' }).exists()).toBe(false)
    expect(mocks.read).toHaveBeenLastCalledWith('account-b')
    expect(overview.props('userKey')).toBe('account-b')
  })

  it('wires refresh and navigation and retains administrator worklist access', async () => {
    store.position = ['admin']
    wrapper = mount(UserHome)
    await flushPromises()
    const overview = wrapper.getComponent({ name: 'HomeOverview' })
    overview.vm.$emit('profile')
    overview.vm.$emit('activities')
    overview.vm.$emit('refresh')
    await flushPromises()
    expect(mocks.push.mock.calls).toEqual([['/me'], ['/activities']])
    expect(mocks.read).toHaveBeenCalledTimes(2)
    expect(wrapper.getComponent({ name: 'UserHomeWorklist' }).props('isAdmin')).toBe(true)
    store.position = ['student']
    await nextTick()
    expect(wrapper.findComponent({ name: 'UserHomeWorklist' }).exists()).toBe(false)
  })

  it('redirects logged-out visitors and does not fetch account data', async () => {
    store.isLogin = false
    wrapper = mount(UserHome)
    await flushPromises()
    expect(mocks.replace).toHaveBeenCalledWith('/user/login')
    expect(mocks.read).not.toHaveBeenCalled()
    expect(wrapper.findComponent({ name: 'HomeOverview' }).exists()).toBe(false)
  })
})

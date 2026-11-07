import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZPrimaryDeviceGate from './ZPrimaryDeviceGate.vue'
import { readMyDevice, setMyPrimaryDevice } from '@/api/user/device'
import type { MyDeviceStatus } from '@/api/user/device'
import { useUserStore } from '@/stores/user'

const { storeState, replaceSpy, successSpy, errorSpy } = vi.hoisted(() => ({
  storeState: {
    isLogin: true,
    _id: 'user-1',
    id: 1,
    position: ['student'] as string[],
    refreshUser: vi.fn(async () => {}),
    removeUser: vi.fn(async () => {})
  },
  replaceSpy: vi.fn(),
  successSpy: vi.fn(),
  errorSpy: vi.fn()
}))

vi.mock('@/api/user/device', () => ({
  readMyDevice: vi.fn(),
  setMyPrimaryDevice: vi.fn()
}))

vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  return { useUserStore: () => reactive(storeState) }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: replaceSpy })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String, width: String },
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [
              h('h2', { class: 'el-dialog-stub__title' }, props.title),
              h('div', { class: 'el-dialog-stub__body' }, slots.default?.()),
              slots.footer?.()
            ])
          : null
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, size: String, loading: Boolean, disabled: Boolean, text: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || undefined,
            onClick: () => {
              if (!props.disabled && !props.loading) {
                emit('click')
              }
            }
          },
          slots.default?.()
        )
    }
  })

  return {
    ElDialog,
    ElButton,
    ElMessage: Object.assign(vi.fn(), {
      success: successSpy,
      error: errorSpy,
      warning: vi.fn(),
      info: vi.fn()
    })
  }
})

function makeStatus(overrides: Partial<MyDeviceStatus> = {}): MyDeviceStatus {
  return {
    xuehai_id: '1001',
    primary_xuehai_id: null,
    device_banned: false,
    device_ban_reason: null,
    device_ban_until: null,
    ...overrides
  }
}

async function mountGate(): Promise<VueWrapper> {
  const wrapper = mount(ZPrimaryDeviceGate)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('.el-button-stub').find((button) => button.text() === text)
}

beforeEach(() => {
  storeState.isLogin = true
  storeState._id = 'user-1'
  storeState.position = ['student']
  storeState.refreshUser.mockClear()
  storeState.removeUser.mockClear()
  replaceSpy.mockClear()
  successSpy.mockClear()
  errorSpy.mockClear()
  vi.mocked(readMyDevice).mockReset()
  vi.mocked(setMyPrimaryDevice).mockReset()
})

describe('主设备强制门', () => {
  it('特权角色在学海设备上未绑定也要弹门', async () => {
    storeState.position = ['admin']
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus())

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    wrapper.unmount()
  })

  it('特权角色在 PC 上未绑定不弹门', async () => {
    storeState.position = ['admin']
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ xuehai_id: '' }))

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('未绑定 + 本机是学海设备：弹门并可按本机号绑定', async () => {
    vi.mocked(readMyDevice)
      .mockResolvedValueOnce(makeStatus())
      .mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))
    vi.mocked(setMyPrimaryDevice).mockResolvedValue(undefined)

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(wrapper.text()).toContain('device.gate.deviceLabel')
    expect(wrapper.text()).toContain('1001')

    await buttonByText(wrapper, 'device.gate.bind')!.trigger('click')
    await flushPromises()

    expect(setMyPrimaryDevice).toHaveBeenCalledWith('1001')
    expect(successSpy).toHaveBeenCalledWith('device.gate.bindSuccess')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('未绑定 + 本机是学海设备：保留退出登录出口', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus())

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    await buttonByText(wrapper, 'device.gate.logout')!.trigger('click')
    await flushPromises()

    expect(storeState.removeUser).toHaveBeenCalled()
    expect(replaceSpy).toHaveBeenCalledWith('/user/login')
    wrapper.unmount()
  })

  it('未绑定 + 本机不是学海设备：只有退出登录一个出口', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ xuehai_id: '' }))

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(buttonByText(wrapper, 'device.gate.bind')).toBeUndefined()

    await buttonByText(wrapper, 'device.gate.logout')!.trigger('click')
    await flushPromises()

    expect(storeState.removeUser).toHaveBeenCalled()
    expect(replaceSpy).toHaveBeenCalledWith('/user/login')
    wrapper.unmount()
  })

  it('已绑定不弹门', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('读取设备状态失败时静默放行', async () => {
    vi.mocked(readMyDevice).mockRejectedValue(new Error('网络异常'))

    const wrapper = await mountGate()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('登录后重新评估并弹门', async () => {
    const store = useUserStore()
    store.isLogin = false
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus())

    const wrapper = await mountGate()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    expect(readMyDevice).not.toHaveBeenCalled()

    // 必须走 reactive 代理赋值，直接改原始对象不会触发 watcher。
    store.isLogin = true
    await flushPromises()

    expect(readMyDevice).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    wrapper.unmount()
  })
})

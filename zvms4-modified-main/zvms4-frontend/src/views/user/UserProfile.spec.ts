import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserProfile from './UserProfile.vue'
import {
  clearMyPrimaryDevice,
  readMyDevice,
  setMyPrimaryDevice
} from '@/api/user/device'
import type { MyDeviceStatus } from '@/api/user/device'
import { listActive } from '@/api/notifications'

const {
  replaceSpy,
  successSpy,
  errorSpy,
  confirmSpy,
  headerSetHeaderSpy,
  storeState
} = vi.hoisted(() => ({
  replaceSpy: vi.fn(),
  successSpy: vi.fn(),
  errorSpy: vi.fn(),
  confirmSpy: vi.fn(),
  headerSetHeaderSpy: vi.fn(),
  storeState: {
    isLogin: true,
    _id: 'user-1',
    id: 20240001,
    name: '张三',
    position: ['student'] as string[],
    class_id: '',
    banned: false,
    resetPassword: vi.fn(),
    removeUser: vi.fn(async () => {})
  }
}))

vi.mock('@/api/user/device', () => ({
  readMyDevice: vi.fn(),
  setMyPrimaryDevice: vi.fn(),
  clearMyPrimaryDevice: vi.fn()
}))

vi.mock('@/api/notifications', () => ({
  listActive: vi.fn()
}))

vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  return { useUserStore: () => reactive(storeState) }
})

vi.mock('@/stores/groups', () => ({
  useGroupsStore: () => ({ fetchGroup: vi.fn(async () => null) })
}))

vi.mock('@/stores/header', () => ({
  useHeaderStore: () => ({ setHeader: headerSetHeaderSpy })
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: replaceSpy, push: vi.fn() })
}))

vi.mock('@vueuse/core', async () => {
  const { ref } = await import('vue')
  return { useDark: () => ref(false) }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params === undefined ? key : `${key} ${JSON.stringify(params)}`,
    locale: { value: 'zh-CN' }
  })
}))

vi.mock('@/views/user/modifyPasswordDialogs', () => ({
  modifyPasswordDialogs: vi.fn()
}))

vi.mock('@/components/markdown/ZMarkdown.vue', () => ({
  default: { name: 'ZMarkdown', template: '<div class="markdown-stub" />' }
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: {
      type: String,
      size: String,
      loading: Boolean,
      disabled: Boolean,
      text: Boolean,
      icon: { type: [Object, String, Function], default: undefined }
    },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || undefined,
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

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: Boolean },
    emits: ['update:modelValue'],
    setup() {
      return () => h('span', { class: 'el-switch-stub' })
    }
  })

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

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: String, default: '' },
      maxlength: { type: Number, default: undefined },
      placeholder: String,
      clearable: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  return {
    ElButton,
    ElIcon,
    ElSwitch,
    ElDialog,
    ElInput,
    ElMessage: Object.assign(vi.fn(), {
      success: successSpy,
      error: errorSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: confirmSpy }
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

async function mountProfile(): Promise<VueWrapper> {
  const wrapper = mount(UserProfile)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('.el-button-stub').find((button) => button.text() === text)
}

beforeEach(() => {
  storeState.isLogin = true
  storeState.position = ['student']
  replaceSpy.mockClear()
  successSpy.mockClear()
  errorSpy.mockClear()
  confirmSpy.mockReset()
  headerSetHeaderSpy.mockClear()
  vi.mocked(readMyDevice).mockReset()
  vi.mocked(setMyPrimaryDevice).mockReset()
  vi.mocked(clearMyPrimaryDevice).mockReset()
  vi.mocked(listActive).mockResolvedValue({ notifications: [], version: 0 } as never)
})

describe('我的页主设备卡', () => {
  it('未绑定 + 本机是学海设备：展示本机号与绑定入口', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus())
    vi.mocked(setMyPrimaryDevice).mockResolvedValue(undefined)

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.currentLabel')
    expect(wrapper.text()).toContain('1001')
    expect(wrapper.text()).toContain('device.profile.none')
    expect(wrapper.text()).toContain('device.profile.unboundHint')

    await buttonByText(wrapper, 'device.profile.bind')!.trigger('click')
    await flushPromises()

    expect(setMyPrimaryDevice).toHaveBeenCalledWith('1001')
    expect(successSpy).toHaveBeenCalledWith('device.profile.bindSuccess')
    expect(readMyDevice).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('未绑定 + 本机不是学海设备：只给引导文案，没有绑定按钮', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ xuehai_id: '' }))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.noDevice')
    expect(wrapper.text()).toContain('device.profile.bindNonDeviceHint')
    expect(buttonByText(wrapper, 'device.profile.bind')).toBeUndefined()
    wrapper.unmount()
  })

  it('已绑定 + 本机是主设备：可更换、可解绑', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))
    vi.mocked(setMyPrimaryDevice).mockResolvedValue(undefined)
    confirmSpy.mockResolvedValue('confirm')
    vi.mocked(clearMyPrimaryDevice).mockResolvedValue(undefined)

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.isPrimary')

    await buttonByText(wrapper, 'device.profile.change')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    await wrapper.find('.el-input-stub').setValue('2002')
    await buttonByText(wrapper, 'device.profile.changeSubmit')!.trigger('click')
    await flushPromises()

    expect(setMyPrimaryDevice).toHaveBeenCalledWith('2002')
    expect(successSpy).toHaveBeenCalledWith('device.profile.changeSuccess')

    await buttonByText(wrapper, 'device.profile.unbind')!.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(clearMyPrimaryDevice).toHaveBeenCalled()
    expect(successSpy).toHaveBeenCalledWith('device.profile.unbindSuccess')
    wrapper.unmount()
  })

  it('已绑定 + 本机不是主设备：更换/解绑禁用并说明原因', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(
      makeStatus({ xuehai_id: '1002', primary_xuehai_id: '1001' })
    )

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.otherDevice')
    expect(wrapper.text()).toContain('device.profile.notPrimaryHint')

    const changeButton = buttonByText(wrapper, 'device.profile.change')!
    const unbindButton = buttonByText(wrapper, 'device.profile.unbind')!
    expect(changeButton.attributes('disabled')).toBeDefined()
    expect(unbindButton.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('更换主设备时校验 1–32 位纯数字', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))

    const wrapper = await mountProfile()

    await buttonByText(wrapper, 'device.profile.change')!.trigger('click')
    await flushPromises()
    await wrapper.find('.el-input-stub').setValue('abc')
    await buttonByText(wrapper, 'device.profile.changeSubmit')!.trigger('click')
    await flushPromises()

    expect(setMyPrimaryDevice).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('device.profile.changeInvalid')
    wrapper.unmount()
  })

  it('本机设备被封禁时以警示样式展示原因与期限', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(
      makeStatus({
        primary_xuehai_id: '1001',
        device_banned: true,
        device_ban_reason: '违规使用',
        device_ban_until: '2026-11-01T12:00:00+08:00'
      })
    )

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.bannedTitle')
    expect(wrapper.text()).toContain('device.profile.bannedReason')
    expect(wrapper.text()).toContain('违规使用')
    expect(wrapper.text()).toContain('device.profile.bannedUntil')
    wrapper.unmount()
  })

  it('读取失败展示重试态', async () => {
    vi.mocked(readMyDevice).mockRejectedValue(new Error('网络异常'))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('device.profile.failed')
    expect(wrapper.text()).toContain('device.profile.retry')
    wrapper.unmount()
  })
})

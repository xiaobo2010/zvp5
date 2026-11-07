import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserLogin from './UserLogin.vue'

const {
  alertMock,
  messageMock,
  setUserMock,
  setUserInformationMock,
  getMaintenanceStatusMock,
  replaceMock,
  tempAuthMock,
  readOneMock,
  userStoreStub
} = vi.hoisted(() => {
  const setUser = vi.fn()
  const setUserInformation = vi.fn()
  return {
    alertMock: vi.fn().mockResolvedValue('confirm'),
    messageMock: vi.fn(),
    setUserMock: setUser,
    setUserInformationMock: setUserInformation,
    getMaintenanceStatusMock: vi.fn(),
    replaceMock: vi.fn(),
    tempAuthMock: vi.fn(),
    readOneMock: vi.fn(),
    // 组件只在 setup 里取一次 store，临时会话要断言 isLogin / shouldResetPassword，
    // 所以这里必须是同一个可变对象，而不是每次调用新建。
    userStoreStub: {
      isLogin: false,
      position: [] as string[],
      shouldResetPassword: false,
      validatePasswordStrength: () => true,
      setUser,
      setUserInformation
    }
  }
})

// element-plus 整包 mock:真实包会引入 theme-chalk 的 .css,node ESM 下无法加载。
// 模板里的 ElForm/ElInput/ElFormItem 由 unplugin 自动导入,所以必须一并导出桩。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElForm = defineComponent({
    name: 'ElForm',
    methods: { validate: () => Promise.resolve(true) },
    setup(_, { slots }) {
      return () => h('form', null, slots.default?.())
    }
  })
  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    setup(_, { slots }) {
      return () => h('div', null, slots.default?.())
    }
  })
  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: [String, Number], default: '' } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })
  const ElButton = defineComponent({
    name: 'ElButton',
    emits: ['click'],
    setup(_, { slots, emit }) {
      return () => h('button', { onClick: () => emit('click') }, slots.default?.())
    }
  })
  const ElCard = defineComponent({
    name: 'ElCard',
    setup(_, { slots }) {
      return () => h('div', null, slots.default?.())
    }
  })

  return {
    ElForm,
    ElFormItem,
    ElInput,
    ElButton,
    ElCard,
    ElMessage: messageMock,
    ElMessageBox: { alert: alertMock }
  }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: unknown) =>
      params === undefined ? key : `${key} ${JSON.stringify(params)}`
  })
}))

vi.mock('@/stores/header', () => ({ useHeaderStore: () => ({ setHeader: vi.fn() }) }))
vi.mock('@/stores/user', () => ({
  useUserStore: () => userStoreStub
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  useRoute: () => ({ query: {} })
}))
vi.mock('@/api', () => ({
  default: {
    user: {
      auth: { useLongTermAuth: vi.fn(), useTempPasswordAuth: tempAuthMock },
      password: { put: vi.fn() },
      readOne: readOneMock
    }
  }
}))
vi.mock('@/api/system/maintenance.ts', () => ({
  getMaintenanceStatus: getMaintenanceStatusMock
}))
vi.mock('@/router/maintenanceGuard', () => ({ resetMaintenanceGuardCache: vi.fn() }))
vi.mock('@/views/user/modifyPasswordDialogs', () => ({ modifyPasswordDialogs: vi.fn() }))
vi.mock('@element-plus/icons-vue', () => ({ ArrowRight: { name: 'ArrowRight' } }))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  userStoreStub.isLogin = false
  userStoreStub.shouldResetPassword = false
  getMaintenanceStatusMock.mockResolvedValue({ data: { data: { enabled: false } } })
  replaceMock.mockResolvedValue(undefined)
  tempAuthMock.mockReset()
  readOneMock.mockReset()
})

/** 构造一个与后端契约一致的封禁 403(AxiosError 结构的最小投影)。 */
function bannedError(reason: string, until: string) {
  const error = new Error('Request failed with status code 403') as Error & {
    response: { status: number; data: { detail: string }; headers: Record<string, string> }
  }
  error.response = {
    status: 403,
    data: { detail: 'USER_BANNED' },
    headers: { 'x-ban-reason': encodeURIComponent(reason), 'x-ban-until': until }
  }
  return error
}

/** 构造设备闸的结构化 403:detail 是对象 {code,message,challenge?}。 */
function deviceError(
  code: string,
  detail: { challenge?: string; message?: string } = {},
  headers: Record<string, string> = {}
) {
  const error = new Error('Request failed with status code 403') as Error & {
    response: { status: number; data: { detail: unknown }; headers: Record<string, string> }
  }
  error.response = {
    status: 403,
    data: { detail: { code, ...detail } },
    headers
  }
  return error
}

const buttonByText = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll('button').find((button) => button.text() === text)

async function submitLogin() {
  const wrapper = mount(UserLogin)
  const inputs = wrapper.findAll('input')
  await inputs[0].setValue('20240001')
  await inputs[1].setValue('Pa55word!')
  await wrapper.find('button').trigger('click')
  await flushPromises()
  return wrapper
}

describe('UserLogin banned feedback', () => {
  it('shows the ban reason and expiry for a timed ban', async () => {
    setUserMock.mockRejectedValueOnce(bannedError('违规使用平板', '1700000000'))
    await submitLogin()

    expect(alertMock).toHaveBeenCalledTimes(1)
    const [content, title, options] = alertMock.mock.calls[0]
    expect(title).toBe('nav.banned.title')
    expect(JSON.stringify(content)).toContain('违规使用平板')
    expect(JSON.stringify(content)).toContain('nav.banned.until')
    expect(options).toMatchObject({
      confirmButtonText: 'nav.banned.ack',
      closeOnClickModal: false,
      showClose: false
    })
  })

  it('shows the permanent line and the fallback reason for a permanent ban', async () => {
    setUserMock.mockRejectedValueOnce(bannedError('', ''))
    await submitLogin()

    expect(alertMock).toHaveBeenCalledTimes(1)
    const [content] = alertMock.mock.calls[0]
    expect(JSON.stringify(content)).toContain('nav.banned.reasonFallback')
    expect(JSON.stringify(content)).toContain('nav.banned.permanent')
    expect(JSON.stringify(content)).not.toContain('nav.banned.until')
  })

  it('ignores non-ban failures so they keep their existing handling', async () => {
    const plain = new Error('bad credentials') as Error & { response: unknown }
    plain.response = { status: 403, data: { detail: 'WRONG_PASSWORD' } }
    setUserMock.mockRejectedValueOnce(plain)
    await submitLogin()

    expect(alertMock).not.toHaveBeenCalled()
  })
})

describe('UserLogin temp password flow', () => {
  const profile = {
    _id: 'oid-1',
    id: '20240001',
    name: '张三',
    roles: ['student'],
    class_id: 'Z2024-1',
    group: ['Z2024-1']
  }

  it('switches to the temp form for DEVICE_NOT_PRIMARY and holds the challenge', async () => {
    setUserMock.mockRejectedValueOnce(
      deviceError('DEVICE_NOT_PRIMARY', { challenge: 'ch-1' })
    )
    const wrapper = await submitLogin()

    // 账号/密码两个输入让位给一个 8 位临时密码输入
    expect(wrapper.findAll('input')).toHaveLength(1)
    expect(wrapper.text()).toContain('nav.login.device.tempTitle')
    // 拦截器已静默,登录页不额外弹通用 toast
    expect(messageMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('exchanges the temp password, installs a temp session and routes on', async () => {
    setUserMock.mockRejectedValueOnce(
      deviceError('DEVICE_NOT_PRIMARY', { challenge: 'ch-1' })
    )
    tempAuthMock.mockResolvedValueOnce({
      token: 'tp-token',
      _id: 'oid-1',
      profile,
      weak_password: false,
      temp_session: true
    })
    const wrapper = await submitLogin()

    await wrapper.find('input').setValue('12345678')
    await buttonByText(wrapper, 'nav.login.device.tempSubmit')!.trigger('click')
    await flushPromises()

    expect(tempAuthMock).toHaveBeenCalledWith('ch-1', '12345678')
    // 临时会话不走 setUser（没有明文密码），直接装用户信息
    expect(setUserInformationMock).toHaveBeenCalledWith(profile)
    expect(userStoreStub.isLogin).toBe(true)
    expect(userStoreStub.shouldResetPassword).toBe(false)
    expect(messageMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'nav.login.device.tempSession' })
    )
    expect(replaceMock).toHaveBeenCalledWith('/user')

    wrapper.unmount()
  })

  it('keeps the temp form retryable after TEMP_PASSWORD_INVALID', async () => {
    setUserMock.mockRejectedValueOnce(
      deviceError('DEVICE_NOT_PRIMARY', { challenge: 'ch-1' })
    )
    tempAuthMock.mockRejectedValueOnce(
      deviceError('TEMP_PASSWORD_INVALID', { message: '临时密码错误或已失效' })
    )
    const wrapper = await submitLogin()

    await wrapper.find('input').setValue('12345678')
    await buttonByText(wrapper, 'nav.login.device.tempSubmit')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('nav.login.device.tempInvalid')
    // 仍在临时模式：输入框与提交按钮都在，可以再试
    expect(wrapper.findAll('input')).toHaveLength(1)
    expect(buttonByText(wrapper, 'nav.login.device.tempSubmit')).toBeTruthy()
    expect(userStoreStub.isLogin).toBe(false)
    expect(replaceMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('shows the device ban alert for DEVICE_BANNED', async () => {
    setUserMock.mockRejectedValueOnce(
      deviceError(
        'DEVICE_BANNED',
        {},
        { 'x-ban-reason': encodeURIComponent('违规使用平板'), 'x-ban-until': '1700000000' }
      )
    )
    await submitLogin()

    expect(alertMock).toHaveBeenCalledTimes(1)
    const [content, title, options] = alertMock.mock.calls[0]
    expect(title).toBe('nav.login.device.bannedTitle')
    expect(JSON.stringify(content)).toContain('违规使用平板')
    expect(JSON.stringify(content)).toContain('nav.banned.until')
    expect(options).toMatchObject({ confirmButtonText: 'nav.login.device.bannedAck' })
  })

  it('tells the user a Xuehai tablet is required for DEVICE_REQUIRED', async () => {
    setUserMock.mockRejectedValueOnce(deviceError('DEVICE_REQUIRED'))
    await submitLogin()

    expect(messageMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'nav.login.device.required', type: 'warning' })
    )
  })
})

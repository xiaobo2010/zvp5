import { defineComponent, h, nextTick, type PropType } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { zhCN as navZhCN } from '@/i18n/locales/nav'
import UserLogin from './UserLogin.vue'

/**
 * 登录页三种失败情形的呈现链路：密码错误（红）、设备闸拦截（黄）、非主设备（黄）。
 * 断言两件事：新的 ZAuthAlert 动画对话框出现，且拦截器的通用 toast 没有叠加。
 */

const mocks = vi.hoisted(() => ({
  setUser: vi.fn(),
  useLongTermAuth: vi.fn(),
  useTempPasswordAuth: vi.fn(),
  validatePasswordStrength: vi.fn(() => true),
  elMessage: vi.fn(),
  elMessageBox: vi.fn(() => Promise.resolve('confirm')),
  routerReplace: vi.fn()
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    isLogin: false,
    position: [] as string[],
    shouldResetPassword: false,
    validatePasswordStrength: mocks.validatePasswordStrength,
    setUser: mocks.setUser,
    setUserInformation: vi.fn(),
    applyAccountThemeAfterLogin: vi.fn()
  })
}))

vi.mock('@/stores/header', () => ({ useHeaderStore: () => ({ setHeader: vi.fn() }) }))

vi.mock('@/api', () => ({
  default: {
    user: {
      auth: {
        useLongTermAuth: mocks.useLongTermAuth,
        useTempPasswordAuth: mocks.useTempPasswordAuth
      },
      readOne: vi.fn(),
      password: { put: vi.fn() }
    }
  }
}))

vi.mock('@/api/system/maintenance.ts', () => ({ getMaintenanceStatus: vi.fn() }))
vi.mock('@/router/maintenanceGuard', () => ({ resetMaintenanceGuardCache: vi.fn() }))
vi.mock('@/views/user/modifyPasswordDialogs', () => ({ modifyPasswordDialogs: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: mocks.routerReplace }),
  useRoute: () => ({ query: {} })
}))

// 只替换 ElMessage/ElMessageBox：前者用来证明「不再叠加旧 toast」，
// 后者是弱密码分支的确认框，不该在本组用例里弹真对话框。
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return { ...actual, ElMessage: mocks.elMessage, ElMessageBox: mocks.elMessageBox }
})

// ElForm 的 validate() 在必填为空时会 reject；本组用例只关心失败弹层，
// 用桩接住校验，避免 watch(user) 里那次校验产生未处理的 rejection。
const ElFormStub = defineComponent({
  name: 'ElForm',
  setup(_, { slots, expose }) {
    expose({ validate: () => Promise.resolve(true) })
    return () => h('form', slots.default?.())
  }
})

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String, prop: String },
  setup(_, { slots }) {
    return () => h('div', slots.default?.())
  }
})

const ElInputStub = defineComponent({
  name: 'ElInput',
  props: {
    modelValue: { type: [String, Number] as PropType<string | number>, default: '' },
    type: { type: String, default: 'text' }
  },
  emits: ['update:modelValue', 'keyup'],
  setup(props, { emit, expose }) {
    let input: HTMLInputElement | null = null
    expose({ focus: () => input?.focus() })
    return () =>
      h('input', {
        ref: (el: unknown) => {
          input = el as HTMLInputElement | null
        },
        type: props.type,
        value: props.modelValue,
        onInput: (event: Event) => {
          emit('update:modelValue', (event.target as HTMLInputElement).value)
        }
      })
  }
})

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { disabled: Boolean, loading: Boolean },
  emits: ['click'],
  setup(props, { emit, slots }) {
    return () =>
      h(
        'button',
        {
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

const i18n = createI18n({
  locale: 'zh-CN',
  fallbackLocale: 'zh-CN',
  messages: { 'zh-CN': { nav: navZhCN } },
  legacy: false
})

let wrapper: VueWrapper | null = null

function mountLogin() {
  wrapper = mount(UserLogin, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        ElForm: ElFormStub,
        ElFormItem: ElFormItemStub,
        ElInput: ElInputStub,
        ElButton: ElButtonStub
      }
    }
  })
  return wrapper
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error('request failed'), {
    response: { status, data: { detail }, headers: {} }
  })
}

function alertEl(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('.z-alert')
}

async function submitLogin(user = '12345678', password = 'StrongPwd1!') {
  const inputs = wrapper!.findAll('input')
  await inputs[0].setValue(user)
  await inputs[1].setValue(password)
  const button = wrapper!.findAll('button')[0]
  await button.trigger('click')
  await flushPromises()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  mocks.validatePasswordStrength.mockReturnValue(true)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('UserLogin 失败情形', () => {
  it('密码错误弹红色对话框，清空密码并把焦点还回密码框', async () => {
    mocks.setUser.mockRejectedValueOnce(httpError(403, '用户名或密码错误'))
    mountLogin()
    await submitLogin()

    const dialog = alertEl()
    expect(dialog).not.toBeNull()
    expect(dialog?.classList.contains('z-alert--danger')).toBe(true)
    expect(dialog?.querySelector('.z-alert__title')?.textContent).toBe(
      navZhCN.login.passwordErrorTitle
    )
    expect(dialog?.querySelector('.z-alert__message')?.textContent).toBe(
      navZhCN.login.passwordErrorMessage
    )
    // 页面侧已接管提示，拦截器那条通用 toast 不得再出现
    expect(mocks.elMessage).not.toHaveBeenCalled()
    // 抑制标记必须沿 store → auth 传到请求配置
    expect(String(mocks.setUser.mock.calls[0][0])).toBe('12345678')
    expect(mocks.setUser.mock.calls[0][2]).toEqual({ suppressErrorToast: true })

    dialog?.querySelector<HTMLButtonElement>('.z-alert__btn--ghost')?.click()
    await nextTick()

    expect(alertEl()).toBeNull()
    expect(wrapper!.findAll('input')[1].element.value).toBe('')
    expect(document.activeElement).toBe(wrapper!.findAll('input')[1].element)
  })

  it('弱密码分支同样带抑制标记，并把密码错误弹成红色对话框', async () => {
    mocks.validatePasswordStrength.mockReturnValue(false)
    mocks.useLongTermAuth.mockRejectedValueOnce(httpError(403, '用户名或密码错误'))
    mountLogin()
    await submitLogin()

    expect(mocks.useLongTermAuth).toHaveBeenCalledWith(
      expect.anything(),
      'StrongPwd1!',
      'short',
      'login',
      { suppressErrorToast: true }
    )
    expect(alertEl()?.classList.contains('z-alert--danger')).toBe(true)
    expect(mocks.elMessage).not.toHaveBeenCalled()
  })

  it('DEVICE_REQUIRED 弹黄色对话框，优先展示后端 message', async () => {
    mocks.setUser.mockRejectedValueOnce(
      httpError(403, { code: 'DEVICE_REQUIRED', message: '请使用学海平板登录' })
    )
    mountLogin()
    await submitLogin()

    const dialog = alertEl()
    expect(dialog?.classList.contains('z-alert--warning')).toBe(true)
    expect(dialog?.querySelector('.z-alert__title')?.textContent).toBe(
      navZhCN.login.device.requiredTitle
    )
    expect(dialog?.querySelector('.z-alert__message')?.textContent).toBe('请使用学海平板登录')
    expect(mocks.elMessage).not.toHaveBeenCalled()

    dialog?.querySelector<HTMLButtonElement>('.z-alert__btn--ghost')?.click()
    await nextTick()
    expect(alertEl()).toBeNull()
  })

  it('DEVICE_NOT_PRIMARY 有 challenge：确认后才切到临时密码表单', async () => {
    mocks.setUser.mockRejectedValueOnce(
      httpError(403, {
        code: 'DEVICE_NOT_PRIMARY',
        message: '本机不是该账号的主学海设备',
        challenge: 'ch-1'
      })
    )
    mountLogin()
    await submitLogin()

    const dialog = alertEl()
    expect(dialog?.classList.contains('z-alert--warning')).toBe(true)
    expect(dialog?.querySelector('.z-alert__message')?.textContent).toBe(
      '本机不是该账号的主学海设备'
    )
    expect(dialog?.querySelector('.z-alert__btn--ghost')?.textContent?.trim()).toBe(
      navZhCN.login.device.notPrimaryAck
    )
    const action = dialog?.querySelector<HTMLButtonElement>('.z-alert__btn--solid')
    expect(action?.textContent?.trim()).toBe(navZhCN.login.device.notPrimaryAction)
    expect(mocks.elMessage).not.toHaveBeenCalled()
    // 弹层还在时不得静默切表单
    expect(wrapper!.text()).not.toContain(navZhCN.login.device.tempTitle)

    action?.click()
    await nextTick()

    expect(alertEl()).toBeNull()
    expect(wrapper!.text()).toContain(navZhCN.login.device.tempTitle)
  })

  it('DEVICE_NOT_PRIMARY 无 challenge：只有一个确认按钮，点击回到登录表单', async () => {
    mocks.setUser.mockRejectedValueOnce(
      httpError(403, { code: 'DEVICE_NOT_PRIMARY', message: '本机不是该账号的主学海设备' })
    )
    mountLogin()
    await submitLogin()

    const dialog = alertEl()
    expect(dialog?.classList.contains('z-alert--warning')).toBe(true)
    expect(dialog?.querySelector('.z-alert__message')?.textContent).toBe(
      navZhCN.login.device.challengeMissing
    )
    expect(dialog?.querySelector('.z-alert__btn--solid')).toBeNull()
    expect(mocks.elMessage).not.toHaveBeenCalled()

    dialog?.querySelector<HTMLButtonElement>('.z-alert__btn--ghost')?.click()
    await nextTick()

    expect(alertEl()).toBeNull()
    expect(wrapper!.findAll('input')).toHaveLength(2)
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserLogin from './UserLogin.vue'

const { alertMock, messageMock, setUserMock, getMaintenanceStatusMock } = vi.hoisted(() => ({
  alertMock: vi.fn().mockResolvedValue('confirm'),
  messageMock: vi.fn(),
  setUserMock: vi.fn(),
  getMaintenanceStatusMock: vi.fn()
}))

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
  useUserStore: () => ({
    isLogin: false,
    position: [] as string[],
    validatePasswordStrength: () => true,
    setUser: setUserMock
  })
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useRoute: () => ({ query: {} })
}))
vi.mock('@/api', () => ({
  default: {
    user: {
      auth: { useLongTermAuth: vi.fn() },
      password: { put: vi.fn() }
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

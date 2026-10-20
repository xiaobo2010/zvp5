import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h, watch, type SetupContext } from 'vue'
import ServerSsh from './ServerSsh.vue'
import { createSshSession } from '@/api/system/ssh.ts'

const {
  openSpy,
  succeedSpy,
  failSpy,
  elMessageErrorSpy,
  elMessageWarningSpy,
  elementComponents
} = vi.hoisted(() => {
  // 本页用了 unplugin-vue-components 的按需注入,编译产物会直接从 'element-plus'
  // 具名导入 El*,所以 mock 必须同时提供这些组件,否则渲染期会报 "No ElTag export"。
  const ElButton = {
    name: 'ElButton',
    props: { disabled: Boolean, type: String, size: String, icon: Object },
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
  }
  const ElDialog = {
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String, width: String },
    emits: ['update:modelValue', 'closed'],
    setup(props: { modelValue?: boolean; title?: string }, ctx: SetupContext) {
      // 真实 ElDialog 是在离场动画结束、DOM 已移除之后才发 closed 的。
      // 桩在 modelValue 落回 false 时补发一次,否则被测页等 @closed 结算的 Promise 永远不 resolve
      // (旧桩不发 closed,没有任何用例能跑通风险框确认之后的分支)。
      watch(
        () => props.modelValue,
        (value, old) => {
          if (!value && old) ctx.emit('closed')
        }
      )
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
              ...(ctx.slots.default?.() ?? []),
              ...(ctx.slots.footer?.() ?? [])
            ])
          : null
    }
  }
  const ElCard = {
    name: 'ElCard',
    template: '<section><slot name="header" /><slot /></section>'
  }
  const ElTag = {
    name: 'ElTag',
    template: '<span class="el-tag-stub"><slot /></span>'
  }
  const ElAlert = {
    name: 'ElAlert',
    props: { title: String },
    template: '<div class="el-alert-stub">{{ title }}</div>'
  }
  const ElSkeleton = { name: 'ElSkeleton', template: '<div class="el-skeleton-stub" />' }
  const ElIcon = { name: 'ElIcon', template: '<span><slot /></span>' }
  const ElSelect = {
    name: 'ElSelect',
    props: { modelValue: [Number, String] },
    template: '<div class="el-select-stub" />'
  }
  const ElOption = { name: 'ElOption', template: '<div />' }
  return {
    openSpy: vi.fn<(scene: string) => Promise<string | null>>(),
    succeedSpy: vi.fn(),
    failSpy: vi.fn(),
    elMessageErrorSpy: vi.fn(),
    elMessageWarningSpy: vi.fn(),
    elementComponents: {
      ElButton,
      ElDialog,
      ElCard,
      ElTag,
      ElAlert,
      ElSkeleton,
      ElIcon,
      ElSelect,
      ElOption
    }
  }
})

// element-plus 整包 mock:被测页只从包里取 ElMessage,组件用上面的最小桩替代。
// 真实包会连带引入 theme-chalk 的 .css,在 node ESM 下无法加载。
vi.mock('element-plus', () => ({
  ...elementComponents,
  ElMessage: Object.assign(vi.fn(), {
    error: elMessageErrorSpy,
    warning: elMessageWarningSpy,
    success: vi.fn(),
    info: vi.fn(),
    closeAll: vi.fn()
  })
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: vi.fn() })
}))

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => ({
    _id: '000000000000000000000001',
    position: ['admin']
  })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('@/api/system/ssh.ts', () => ({
  createSshSession: vi.fn(),
  buildSshWsUrl: vi.fn((ticket: string) => `ws://test/system/ssh/ws?ticket=${ticket}`)
}))

vi.mock('@/components/system/ZSystemAuthDialog.vue', () => ({
  default: {
    name: 'ZSystemAuthDialogStub',
    setup(_props: unknown, { expose }: { expose: (api: Record<string, unknown>) => void }) {
      expose({ open: openSpy, succeed: succeedSpy, fail: failSpy })
      return () => h('div', { class: 'auth-dialog-stub' })
    }
  }
}))

vi.mock('@xterm/xterm', () => ({
  Terminal: class TerminalStub {
    cols = 80
    rows = 24
    options: Record<string, unknown> = {}
    open = vi.fn()
    write = vi.fn()
    onData = vi.fn()
    onResize = vi.fn()
    dispose = vi.fn()
    loadAddon = vi.fn()
    reset = vi.fn()
    focus = vi.fn()
  }
}))

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class FitAddonStub {
    fit = vi.fn()
  }
}))

const WARNED_KEY = 'zv.ssh.warned.000000000000000000000001'
const CODE = '123456'

class WebSocketStub {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: WebSocketStub[] = []

  readyState: number = WebSocketStub.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(readonly url: string) {
    WebSocketStub.instances.push(this)
  }

  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = WebSocketStub.CLOSED
  })
}

function mountPage(): VueWrapper {
  return mount(ServerSsh)
}

/** 空态按钮就是带 mt-2 的那个(header 主按钮是同一个文案,不能按文案取)。 */
function emptyStateButton(wrapper: VueWrapper) {
  const button = wrapper.find('button.mt-2')
  if (!button.exists()) throw new Error('empty-state start button not rendered')
  return button
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === text)
  if (!button) throw new Error(`button "${text}" not rendered`)
  return button
}

beforeEach(() => {
  localStorage.clear()
  WebSocketStub.instances = []
  openSpy.mockReset()
  succeedSpy.mockReset()
  failSpy.mockReset()
  vi.mocked(createSshSession).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageWarningSpy.mockReset()
  vi.stubGlobal('WebSocket', WebSocketStub)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

it('renders the idle empty state with an enabled start button', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(wrapper.text()).toContain('server.ssh.statusIdle')
  expect(wrapper.text()).toContain('server.ssh.emptyTitle')
  const button = emptyStateButton(wrapper)
  expect(button.text()).toBe('server.ssh.startButton')
  expect(button.attributes('disabled')).toBeUndefined()

  wrapper.unmount()
})

it('shows the root-risk dialog on first click when localStorage has no warned key', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  const dialog = wrapper.find('.el-dialog-stub')
  expect(dialog.exists()).toBe(true)
  expect(dialog.attributes('data-title')).toBe('server.ssh.riskTitle')
  expect(wrapper.text()).toContain('server.ssh.riskAck')

  wrapper.unmount()
})

it('cancelling the risk dialog does not start a session and returns to idle', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  await buttonByText(wrapper, 'server.ssh.riskCancel').trigger('click')
  await flushPromises()

  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  expect(openSpy).not.toHaveBeenCalled()
  expect(createSshSession).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('server.ssh.statusIdle')
  expect(wrapper.text()).toContain('server.ssh.emptyTitle')
  expect(localStorage.getItem(WARNED_KEY)).toBeNull()

  wrapper.unmount()
})

it('acknowledging the risk dialog opens the system auth dialog and remembers the choice', async () => {
  openSpy.mockResolvedValue(null)
  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  await buttonByText(wrapper, 'server.ssh.riskAck').trigger('click')
  await flushPromises()

  expect(localStorage.getItem(WARNED_KEY)).toBe('1')
  expect(openSpy).toHaveBeenCalledTimes(1)
  expect(openSpy).toHaveBeenCalledWith('server.ssh.authScene')
  expect(createSshSession).not.toHaveBeenCalled()

  wrapper.unmount()
})

// 回归:风险框返回 true 后必须真的把授权框打开。
// 旧实现同批渲染导致授权框 overlay 被拆掉,服务端连 POST 都收不到,这里钉住后续链路。
it('continues to the auth dialog after the risk dialog closes', async () => {
  openSpy.mockResolvedValue(null)
  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

  await buttonByText(wrapper, 'server.ssh.riskAck').trigger('click')
  await flushPromises()

  // 风险框先离场,授权框才打开(顺序即修复点)
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  expect(openSpy).toHaveBeenCalledTimes(1)
  expect(openSpy).toHaveBeenCalledWith('server.ssh.authScene')

  wrapper.unmount()
})

// 右上角 X / ESC 只改 modelValue,不走 ack/cancel:必须按取消结算,Promise 不能挂着。
it('settles as cancelled when the risk dialog is dismissed without a decision', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  wrapper.findComponent({ name: 'ElDialog' }).vm.$emit('update:modelValue', false)
  await flushPromises()

  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  expect(openSpy).not.toHaveBeenCalled()
  expect(createSshSession).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('server.ssh.statusIdle')
  expect(localStorage.getItem(WARNED_KEY)).toBeNull()

  wrapper.unmount()
})

it('skips the risk dialog when the warned key is already stored', async () => {
  localStorage.setItem(WARNED_KEY, '1')
  openSpy.mockResolvedValue(null)
  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  expect(openSpy).toHaveBeenCalledTimes(1)
  expect(openSpy).toHaveBeenCalledWith('server.ssh.authScene')

  wrapper.unmount()
})

it('exchanges a six-digit code for a ticket', async () => {
  localStorage.setItem(WARNED_KEY, '1')
  openSpy.mockResolvedValue(CODE)
  vi.mocked(createSshSession).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { ticket: 'ticket-1', expires_in: 120 } }
  } as never)

  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  expect(createSshSession).toHaveBeenCalledTimes(1)
  expect(createSshSession).toHaveBeenCalledWith(CODE)
  expect(succeedSpy).toHaveBeenCalledTimes(1)
  expect(WebSocketStub.instances).toHaveLength(1)
  expect(WebSocketStub.instances[0].url).toContain('ticket=ticket-1')

  wrapper.unmount()
})

it('resets to idle without calling the API when the auth dialog resolves null', async () => {
  localStorage.setItem(WARNED_KEY, '1')
  openSpy.mockResolvedValue(null)

  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  expect(createSshSession).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('server.ssh.statusIdle')
  expect(wrapper.text()).not.toContain('server.ssh.statusConnecting')
  expect(wrapper.find('.el-skeleton-stub').exists()).toBe(false)
  expect(emptyStateButton(wrapper).attributes('disabled')).toBeUndefined()

  wrapper.unmount()
})

import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h, watch, type SetupContext } from 'vue'
import ServerSsh from './ServerSsh.vue'
import { createSshSession, suggestSshCommand } from '@/api/system/ssh.ts'
import { listModels } from '@/api/ai/chat.ts'

const {
  openSpy,
  succeedSpy,
  failSpy,
  elMessageErrorSpy,
  elMessageWarningSpy,
  elMessageBoxConfirmSpy,
  elementComponents,
  terminalInstances
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
  const ElInput = {
    name: 'ElInput',
    props: { modelValue: [String, Number] },
    emits: ['update:modelValue'],
    template:
      '<textarea class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
  const ElCheckbox = {
    name: 'ElCheckbox',
    props: { modelValue: Boolean, disabled: Boolean },
    emits: ['update:modelValue'],
    template:
      '<label class="el-checkbox-stub"><input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', $event.target.checked)" /><slot /></label>'
  }
  return {
    openSpy: vi.fn<(scene: string) => Promise<string | null>>(),
    succeedSpy: vi.fn(),
    failSpy: vi.fn(),
    elMessageErrorSpy: vi.fn(),
    elMessageWarningSpy: vi.fn(),
    elMessageBoxConfirmSpy: vi.fn(),
    elementComponents: {
      ElButton,
      ElDialog,
      ElCard,
      ElTag,
      ElAlert,
      ElSkeleton,
      ElIcon,
      ElSelect,
      ElOption,
      ElInput,
      ElCheckbox
    },
    terminalInstances: [] as Array<{
      buffer: { active: { type: string; viewportY: number; length: number } }
      modes: { mouseTrackingMode: string }
      scrollLines: ReturnType<typeof vi.fn>
      scrollPages: ReturnType<typeof vi.fn>
      scrollToBottom: ReturnType<typeof vi.fn>
      focus: ReturnType<typeof vi.fn>
      attachCustomKeyEventHandler: ReturnType<typeof vi.fn>
    }>
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
  }),
  ElMessageBox: { confirm: elMessageBoxConfirmSpy }
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
  suggestSshCommand: vi.fn(),
  buildSshWsUrl: vi.fn((ticket: string) => `ws://test/system/ssh/ws?ticket=${ticket}`)
}))

vi.mock('@/api/ai/chat.ts', () => ({
  listModels: vi.fn()
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
    static instances: unknown[] = []

    cols = 80
    rows = 24
    options: Record<string, unknown> = {}
    buffer = { active: { type: 'normal', viewportY: 0, length: 24 } }
    modes = { mouseTrackingMode: 'none' as const }
    open = vi.fn()
    write = vi.fn()
    onData = vi.fn()
    onResize = vi.fn()
    dispose = vi.fn()
    loadAddon = vi.fn()
    reset = vi.fn()
    focus = vi.fn()
    attachCustomKeyEventHandler = vi.fn()
    scrollLines = vi.fn()
    scrollPages = vi.fn()
    scrollToBottom = vi.fn()

    constructor() {
      terminalInstances.push(this)
    }
    // 收集给测试断言用;vi.mock 工厂里的属性在运行时仍指向 hoisted 变量
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

const SUGGEST = {
  command: 'df -h',
  explanation: '列出各挂载点的磁盘使用情况',
  model: 'gpt-test',
  request_id: 'req-1'
}

/** 打开 AI 面板并填入意图 + 点生成,返回面板内文案。 */
async function openSuggestPanel(wrapper: VueWrapper, intent = '看磁盘占用') {
  await buttonByText(wrapper, 'server.ssh.suggest.open').trigger('click')
  await flushPromises()
  await wrapper.find('#ssh-suggest-intent').setValue(intent)
  await buttonByText(wrapper, 'server.ssh.suggest.generate').trigger('click')
  await flushPromises()
}

/** 走完授权 + 建连,并把 WS 置为已连接。 */
async function connectTerminal(wrapper: VueWrapper) {
  localStorage.setItem(WARNED_KEY, '1')
  openSpy.mockResolvedValue(CODE)
  vi.mocked(createSshSession).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { ticket: 'ticket-1', expires_in: 120 } }
  } as never)

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  const ws = WebSocketStub.instances[0]!
  ws.readyState = WebSocketStub.OPEN
  ws.onopen?.()
  await flushPromises()
  return ws
}

beforeEach(() => {
  localStorage.clear()
  WebSocketStub.instances = []
  terminalInstances.length = 0
  openSpy.mockReset()
  succeedSpy.mockReset()
  failSpy.mockReset()
  vi.mocked(createSshSession).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageWarningSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue(undefined)
  vi.mocked(suggestSshCommand).mockReset()
  vi.mocked(listModels).mockReset()
  vi.mocked(listModels).mockRejectedValue(new Error('no models'))
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

// 滚轮翻历史:远端开启鼠标上报时滚轮会被拦截改为翻 scrollback,
// 翻出底部后应显示「回到底部」,点击要真正 scrollToBottom 并聚焦终端。
it('scrolls into history on wheel and offers the back-to-bottom button', async () => {
  localStorage.setItem(WARNED_KEY, '1')
  openSpy.mockResolvedValue(CODE)
  vi.mocked(createSshSession).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { ticket: 'ticket-1', expires_in: 120 } }
  } as never)

  const wrapper = mountPage()
  await flushPromises()

  await emptyStateButton(wrapper).trigger('click')
  await flushPromises()

  const term = terminalInstances[terminalInstances.length - 1]!
  expect(term.attachCustomKeyEventHandler).toHaveBeenCalled()

  // 视口在底部时不显示按钮
  expect(wrapper.find('.back-to-bottom').exists()).toBe(false)

  // 模拟远端开启鼠标上报 + 视口已翻离底部
  term.modes.mouseTrackingMode = 'drag'
  term.buffer.active.length = 50
  term.buffer.active.viewportY = 0

  const WheelEventCtor =
    (globalThis as { WheelEvent?: typeof WheelEvent }).WheelEvent ??
    (globalThis as unknown as { MouseEvent: typeof MouseEvent }).MouseEvent
  const box = wrapper.find('.terminal-box')
  box.element.dispatchEvent(
    new WheelEventCtor('wheel', { deltaY: 120, cancelable: true }) as Event
  )
  await flushPromises()

  expect(term.scrollLines).toHaveBeenCalledWith(3)
  const backToBottom = wrapper.find('.back-to-bottom')
  expect(backToBottom.exists()).toBe(true)

  await backToBottom.trigger('click')
  expect(term.scrollToBottom).toHaveBeenCalledTimes(1)
  expect(term.focus).toHaveBeenCalled()

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

// ---------- AI 生成命令面板 ----------
it('shows the generated command and its explanation in the panel', async () => {
  vi.mocked(suggestSshCommand).mockResolvedValue({
    data: { status: 'ok', code: 200, data: SUGGEST }
  } as never)

  const wrapper = mountPage()
  await flushPromises()

  await openSuggestPanel(wrapper, '看磁盘占用')

  expect(suggestSshCommand).toHaveBeenCalledTimes(1)
  expect(suggestSshCommand).toHaveBeenCalledWith('看磁盘占用', undefined, undefined)
  expect((wrapper.find('.suggest__command').element as HTMLTextAreaElement).value).toBe('df -h')
  expect(wrapper.text()).toContain(SUGGEST.explanation)

  wrapper.unmount()
})

it('sends no input frame until the insert button is clicked', async () => {
  vi.mocked(suggestSshCommand).mockResolvedValue({
    data: { status: 'ok', code: 200, data: SUGGEST }
  } as never)

  const wrapper = mountPage()
  await flushPromises()
  const ws = await connectTerminal(wrapper)

  await openSuggestPanel(wrapper)

  // 生成只落面板文本,一帧都不往终端发
  const inputFrames = () =>
    ws.send.mock.calls
      .map(([raw]) => JSON.parse(String(raw)) as { type?: string })
      .filter((frame) => frame.type === 'input')
  expect(inputFrames()).toHaveLength(0)

  await buttonByText(wrapper, 'server.ssh.suggest.insert').trigger('click')
  await flushPromises()

  const frames = ws.send.mock.calls
    .map(([raw]) => JSON.parse(String(raw)) as { type?: string; data?: string })
    .filter((frame) => frame.type === 'input')
  expect(frames).toHaveLength(1)
  // 默认只写入,不回车
  expect(frames[0]!.data).toBe('df -h')
  expect(frames[0]!.data).not.toContain('\r')

  wrapper.unmount()
})

it('disables the insert button while the terminal is not connected', async () => {
  vi.mocked(suggestSshCommand).mockResolvedValue({
    data: { status: 'ok', code: 200, data: SUGGEST }
  } as never)

  const wrapper = mountPage()
  await flushPromises()

  await openSuggestPanel(wrapper)

  expect(buttonByText(wrapper, 'server.ssh.suggest.insert').attributes('disabled')).toBeDefined()

  wrapper.unmount()
})

it('shows a localized error for a rate-limited suggest request and keeps the session', async () => {
  vi.mocked(suggestSshCommand).mockRejectedValue({
    response: { status: 429, data: { detail: 'AI_RATE_LIMITED' } }
  })

  const wrapper = mountPage()
  await flushPromises()

  await openSuggestPanel(wrapper)

  const text = wrapper.text()
  expect(text).toContain('server.ssh.suggest.error.rateLimited')
  expect(text).not.toContain('AI_RATE_LIMITED')
  expect(wrapper.find('.suggest__command').exists()).toBe(false)
  // 会话状态未被这次失败改动
  expect(wrapper.text()).toContain('server.ssh.statusIdle')
  expect(WebSocketStub.instances).toHaveLength(0)

  wrapper.unmount()
})

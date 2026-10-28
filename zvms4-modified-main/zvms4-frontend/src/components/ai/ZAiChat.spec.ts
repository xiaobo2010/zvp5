import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiChat from './ZAiChat.vue'
import { useUserStore } from '@/stores/user'
import {
  cancelRun,
  confirmProposal,
  createConversation,
  deleteConversation,
  fetchActiveRuns,
  fetchPendingProposals,
  fetchRunDetail,
  getConversation,
  listConversations,
  listMessages,
  listModels,
  rejectProposal,
  submitRun,
  updateConversation
} from '@/api/ai'
import type { AiAgentProposal, AiConversation, AiRun } from '@/../types/ai'

/**
 * 对话页（会话化之后）。
 *
 * 覆盖六件与旧版不同的事：会话栏与切换、上下文水位条、压缩分隔线、
 * 智能体提交后不阻塞、轮询到终态就停、以及进入页面接回在跑的任务。
 * 提案确认与双人动态码的既有行为一并守住。
 */

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listModels: vi.fn(),
    fetchPendingProposals: vi.fn(),
    confirmProposal: vi.fn(),
    rejectProposal: vi.fn(),
    fetchRunDetail: vi.fn(),
    getConversation: vi.fn(),
    listConversations: vi.fn(),
    listMessages: vi.fn(),
    createConversation: vi.fn(),
    updateConversation: vi.fn(),
    deleteConversation: vi.fn(),
    submitRun: vi.fn(),
    cancelRun: vi.fn(),
    fetchActiveRuns: vi.fn()
  }
})

const { elMessageSuccessSpy, elMessageErrorSpy, authOpenSpy, authSucceedSpy, authFailSpy } =
  vi.hoisted(() => ({
    elMessageSuccessSpy: vi.fn(),
    elMessageErrorSpy: vi.fn(),
    authOpenSpy: vi.fn(),
    authSucceedSpy: vi.fn(),
    authFailSpy: vi.fn()
  }))

// 动态码弹窗复用系统页那一个（它内部用 ElDialog，本文件的 element-plus 桩里没有），
// 这里换成可控的桩：open 返回动态码，succeed/fail 记录调用。
vi.mock('@/components/system/ZSystemAuthDialog.vue', async () => {
  const { h } = await import('vue')
  return {
    default: {
      name: 'ZSystemAuthDialogStub',
      setup(_props: unknown, { expose }: { expose: (api: Record<string, unknown>) => void }) {
        expose({ open: authOpenSpy, succeed: authSucceedSpy, fail: authFailSpy })
        return () => h('div', { class: 'auth-dialog-stub' })
      }
    }
  }
})

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) =>
        params === undefined ? key : `${key} ${JSON.stringify(params)}`
    })
  }
})

vi.mock('element-plus', async () => {
  const { defineComponent, h, nextTick, watch } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, size: String, loading: Boolean, disabled: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || undefined,
            'data-loading': props.loading ? 'true' : 'false',
            onClick: () => {
              if (props.disabled) {
                return
              }
              emit('click')
            }
          },
          slots.default?.()
        )
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: [String, Number], default: '' }, type: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('textarea', {
          class: 'el-input-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
        })
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: { type: [String, Number], default: '' }, placeholder: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: { type: Boolean, default: false } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('button', {
          class: 'el-switch-stub',
          'data-checked': props.modelValue ? 'true' : 'false',
          onClick: () => emit('update:modelValue', !props.modelValue)
        })
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue', 'closed'],
    setup(props, { emit, slots, attrs }) {
      // 真实 ElDialog 在离场动画结束后抛 closed；桩里退化成下一拍，
      // 用来验证「结论只在 closed 才结算」这条约定。
      watch(
        () => props.modelValue,
        (open, prev) => {
          if (prev && !open) {
            void nextTick(() => emit('closed'))
          }
        }
      )
      return () =>
        props.modelValue
          ? h('div', { class: ['el-dialog-stub', attrs.class] }, [
              slots.default?.(),
              slots.footer?.()
            ])
          : null
    }
  })

  const ElDrawer = defineComponent({
    name: 'ElDrawer',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    setup(props, { emit, slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-drawer-stub' }, slots.default?.())
          : null
    }
  })

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElButton,
    ElInput,
    ElSelect,
    ElSwitch,
    ElDialog,
    ElDrawer,
    ElIcon: defineComponent({
      name: 'ElIcon',
      setup(_, { slots }) {
        return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
      }
    }),
    ElOption: defineComponent({
      name: 'ElOption',
      props: { label: String, value: [String, Number] },
      setup() {
        return () => null
      }
    }),
    ElTag: defineComponent({
      name: 'ElTag',
      props: { type: String, size: String },
      setup(_, { slots }) {
        return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
      }
    }),
    ElEmpty: defineComponent({
      name: 'ElEmpty',
      props: { description: String },
      setup(props) {
        return () => h('div', { class: 'el-empty-stub' }, props.description)
      }
    }),
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: Object.assign(vi.fn(), {
      confirm: vi.fn(async () => 'confirm'),
      alert: vi.fn(async () => 'confirm')
    })
  }
})

/* ---------------------------------------------------------------------------
   夹具
   --------------------------------------------------------------------------- */

function conversation(overrides: Partial<AiConversation> = {}): AiConversation {
  return {
    oid: 'agent1',
    mode: 'agent',
    title: '查图书馆',
    model: 'gpt-4o-mini',
    status: 'active',
    tokens: { prompt: 120, completion: 30, total: 150 },
    context: { tokens: 120, limit: 32768, ratio: 0.0037 },
    compaction: { upto_seq: 0, summarized: false, degraded_at: null },
    message_count: 0,
    created_at: '2026-10-25T00:00:00',
    updated_at: '2026-10-25T01:00:00',
    ...overrides
  }
}

function run(overrides: Partial<AiRun> = {}): AiRun {
  return {
    oid: 'run1',
    conversation_oid: 'agent1',
    status: 'running',
    model: 'gpt-4o-mini',
    stop_reason: null,
    rounds: 1,
    error_code: null,
    error_message: null,
    result_message_oid: null,
    prompt_tokens: 100,
    completion_tokens: 20,
    total_tokens: 120,
    cancel_requested: false,
    created_at: '2026-10-25T00:02:00',
    started_at: '2026-10-25T00:02:01',
    finished_at: null,
    ...overrides
  }
}

/** 提案骨架：新契约多了 preview / requires_dual_auth / dual_auth_cached。 */
function proposal(overrides: Partial<AiAgentProposal> = {}): AiAgentProposal {
  return {
    oid: 'a'.repeat(32),
    tool_name: 'update_member_duration',
    args_summary: '{"duration":6}',
    preview: null,
    requires_dual_auth: false,
    dual_auth_cached: false,
    expires_at: serverTime(10 * 60_000),
    ...overrides
  }
}

/** 后端 expires_at 的形态：本地时间、空格分隔、不带时区。 */
function serverTime(offsetMs: number): string {
  const at = new Date(Date.now() + offsetMs)
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  )
}

/** 账号 u1 的风险确认标识（按账号隔离）。 */
const RISK_KEY = 'zvms.agent.riskAck.u1'

/* ---------------------------------------------------------------------------
   工具
   --------------------------------------------------------------------------- */

/** 假定时器下的「等一切落定」：推进 0 毫秒并把微任务链跑完。 */
async function settle(times = 8): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await vi.advanceTimersByTimeAsync(0)
  }
}

function mountPage(props: Record<string, unknown> = {}): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  // 账号固定成 u1：风险标识按账号隔离，key 才有确定的形状
  useUserStore(pinia)._id = 'u1'
  return mount(ZAiChat, { props, global: { plugins: [pinia] } })
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

async function sendText(wrapper: VueWrapper, text: string) {
  await wrapper.find('.el-input-stub').setValue(text)
  await findButton(wrapper, 'ai.chat.send')!.trigger('click')
  await settle()
}

beforeEach(() => {
  vi.useFakeTimers()
  // 风险标识默认已确认：既有用例不被首次使用弹窗挡住；
  // 要验证弹窗的用例自己在挂载前把它清掉。
  localStorage.clear()
  localStorage.setItem(RISK_KEY, '1')
  vi.mocked(listModels).mockReset()
  vi.mocked(fetchPendingProposals).mockReset()
  vi.mocked(confirmProposal).mockReset()
  vi.mocked(rejectProposal).mockReset()
  vi.mocked(fetchRunDetail).mockReset()
  vi.mocked(getConversation).mockReset()
  vi.mocked(listConversations).mockReset()
  vi.mocked(listMessages).mockReset()
  vi.mocked(createConversation).mockReset()
  vi.mocked(updateConversation).mockReset()
  vi.mocked(deleteConversation).mockReset()
  vi.mocked(submitRun).mockReset()
  vi.mocked(cancelRun).mockReset()
  vi.mocked(fetchActiveRuns).mockReset()
  authOpenSpy.mockReset()
  authSucceedSpy.mockReset()
  authFailSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageErrorSpy.mockReset()

  vi.mocked(listModels).mockResolvedValue({
    object: 'list',
    data: [{ id: 'gpt-4o-mini', object: 'model', owned_by: 'shared' }]
  })
  vi.mocked(listConversations).mockResolvedValue({
    conversations: [
      conversation(),
      conversation({ oid: 'agent2', title: '改一下时长' }),
      conversation({ oid: 'legacy-chat', mode: 'chat', title: '历史对话' })
    ],
    next_cursor: null,
    size: 3
  })
  vi.mocked(listMessages).mockResolvedValue({ messages: [], has_more: false, total: 0 })
  vi.mocked(fetchActiveRuns).mockResolvedValue([])
  vi.mocked(fetchPendingProposals).mockResolvedValue({ proposals: [] })
  vi.mocked(getConversation).mockResolvedValue(conversation())
  vi.mocked(createConversation).mockResolvedValue(conversation({ oid: 'new1' }))
  vi.mocked(updateConversation).mockResolvedValue(conversation())
  vi.mocked(deleteConversation).mockResolvedValue({ deleted: true, messages: 0, runs: 0 })
  vi.mocked(fetchRunDetail).mockResolvedValue({
    run: run({ status: 'succeeded', stop_reason: 'completed' }),
    events: [],
    proposals: []
  })
  vi.mocked(submitRun).mockResolvedValue({
    run_id: 'run1',
    message_oid: 'm1',
    status: 'queued'
  })
})

afterEach(() => {
  vi.useRealTimers()
})

/* ---------------------------------------------------------------------------
   用例
   --------------------------------------------------------------------------- */

describe('会话栏', () => {
  it('列出标题、相对时间与累计 token', async () => {
    const wrapper = mountPage()
    await settle()

    const items = wrapper.findAll('.ai-sessions__item')
    // 历史 chat 会话不再出现在界面（后端不删，前端筛掉）。
    expect(items).toHaveLength(2)
    expect(items[0]!.text()).toContain('查图书馆')
    expect(items[0]!.text()).toContain('ai.chat.sessions.tokens')
    expect(items[0]!.text()).toContain('150')
    expect(wrapper.text()).not.toContain('历史对话')

    wrapper.unmount()
  })

  it('没有会话时给空态引导', async () => {
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [],
      next_cursor: null,
      size: 0
    })
    const wrapper = mountPage()
    await settle()

    expect(wrapper.find('.ai-sessions .el-empty-stub').exists()).toBe(true)
    expect(wrapper.text()).toContain('ai.chat.sessions.empty')
    // 没有会话就没法开聊：提示先新建一个。
    expect(wrapper.text()).toContain('ai.chat.sessions.pickFirst')

    wrapper.unmount()
  })

  it('空态里点「新建会话」：建一个并切过去', async () => {
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [],
      next_cursor: null,
      size: 0
    })
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.chat.sessions.new')!.trigger('click')
    await settle()

    expect(createConversation).toHaveBeenCalledWith('agent')
    expect(listMessages).toHaveBeenCalledWith('new1', expect.objectContaining({ afterSeq: 0 }))

    wrapper.unmount()
  })

  it('切换会话：拉那个会话的消息', async () => {
    const wrapper = mountPage()
    await settle()
    vi.mocked(listMessages).mockClear()

    await wrapper.findAll('.ai-sessions__main')[1]!.trigger('click')
    await settle()

    expect(listMessages).toHaveBeenCalledWith('agent2', expect.objectContaining({ afterSeq: 0 }))
    expect(wrapper.findAll('.ai-sessions__item')[1]!.classes()).toContain('is-active')

    wrapper.unmount()
  })

  it('删除会话：先确认，再删', async () => {
    const wrapper = mountPage()
    await settle()

    await wrapper
      .findAll('.ai-sessions__item')[1]!
      .find('button[title="ai.chat.sessions.remove"]')!
      .trigger('click')
    await settle()

    expect(deleteConversation).toHaveBeenCalledWith('agent2')
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.chat.sessions.deletedNotice')

    wrapper.unmount()
  })
})

describe('上下文水位', () => {
  it('按 token / limit 画比例，90% 起换警示色', async () => {
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [
        conversation({
          context: { tokens: 900, limit: 1000, ratio: 0.9 },
          compaction: { upto_seq: 0, summarized: false, degraded_at: null }
        })
      ],
      next_cursor: null,
      size: 1
    })
    const wrapper = mountPage()
    await settle()

    const rail = wrapper.find('.ai-chat__context .yg-progress__rail')
    expect(rail.exists()).toBe(true)
    expect(rail.attributes('style')).toContain('--yg-progress-value: 90')
    expect(wrapper.find('.ai-chat__context .yg-progress--warning').exists()).toBe(true)
    expect(wrapper.text()).toContain('ai.chat.sessions.context.warning')

    wrapper.unmount()
  })

  it('水位低时是中性色', async () => {
    const wrapper = mountPage()
    await settle()

    const rail = wrapper.find('.ai-chat__context .yg-progress__rail')
    expect(rail.attributes('style')).toContain('--yg-progress-value: 0')
    expect(wrapper.find('.ai-chat__context .yg-progress--warning').exists()).toBe(false)

    wrapper.unmount()
  })
})

describe('压缩分隔线', () => {
  it('在压缩点画线并说明更早有多少条被压缩', async () => {
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [
        conversation({
          compaction: { upto_seq: 2, summarized: true, degraded_at: null }
        })
      ],
      next_cursor: null,
      size: 1
    })
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        { oid: 'm1', seq: 1, role: 'user', content: '第一条', created_at: '2026-10-25T00:00:00' },
        { oid: 'm2', seq: 2, role: 'assistant', content: '第二条', created_at: '2026-10-25T00:01:00' },
        { oid: 'm3', seq: 3, role: 'user', content: '第三条', created_at: '2026-10-25T00:02:00' }
      ],
      has_more: false,
      total: 3
    })
    const wrapper = mountPage()
    await settle()

    const divider = wrapper.find('.ai-chat__compaction')
    expect(divider.exists()).toBe(true)
    expect(divider.text()).toContain('ai.chat.sessions.context.compacted')
    expect(divider.text()).toContain('2')

    wrapper.unmount()
  })
})

describe('智能体模式', () => {
  it('提交后立刻返回，不等任务跑完', async () => {
    const wrapper = mountPage()
    await settle()

    await sendText(wrapper, '查一下图书馆')

    expect(submitRun).toHaveBeenCalledWith(
      'agent1',
      '查一下图书馆',
      expect.objectContaining({ model: 'gpt-4o-mini' })
    )
    // 提交就结束了：按钮不再处于加载态，页面显示「后台执行中」。
    expect(findButton(wrapper, 'ai.chat.send')!.attributes('data-loading')).toBe('false')
    expect(wrapper.text()).toContain('ai.agent.run.background')

    wrapper.unmount()
  })

  it('轮询到终态就停：不再发请求', async () => {
    const wrapper = mountPage()
    await settle()
    await sendText(wrapper, '查一下图书馆')

    await vi.advanceTimersByTimeAsync(1500)
    await settle()
    const calls = vi.mocked(fetchRunDetail).mock.calls.length
    expect(calls).toBeGreaterThan(0)

    await vi.advanceTimersByTimeAsync(10_000)
    await settle()

    expect(vi.mocked(fetchRunDetail).mock.calls.length).toBe(calls)
    expect(wrapper.text()).toContain('ai.agent.run.status.succeeded')

    wrapper.unmount()
  })

  it('工具留痕与提案卡按原来的观感渲染', async () => {
    vi.mocked(fetchRunDetail).mockResolvedValue({
      run: run({ status: 'succeeded', stop_reason: 'completed', rounds: 2 }),
      events: [
        {
          round_no: 1,
          tool_name: 'search_activities',
          tool_kind: 'read',
          args_summary: '{"query":"图书馆"}',
          result_summary: '找到 2 个活动',
          status: 'executed'
        }
      ],
      proposals: [proposal({ oid: 'b'.repeat(32) })]
    })
    const wrapper = mountPage()
    await settle()
    await sendText(wrapper, '查一下图书馆并改时长')

    await vi.advanceTimersByTimeAsync(1500)
    await settle()

    expect(wrapper.text()).toContain('ai.agent.tools.search_activities')
    expect(wrapper.text()).toContain('ai.agent.tools.update_member_duration')
    // 结果默认收起
    expect(wrapper.text()).not.toContain('找到 2 个活动')
    await wrapper.find('.ai-agent__event-row')!.trigger('click')
    expect(wrapper.text()).toContain('找到 2 个活动')

    wrapper.unmount()
  })

  it('中止运行调取消接口', async () => {
    vi.mocked(cancelRun).mockResolvedValue({ status: 'cancelled' })
    const wrapper = mountPage()
    await settle()
    await sendText(wrapper, '查一下图书馆')

    await findButton(wrapper, 'ai.agent.run.cancel')!.trigger('click')
    await settle()

    expect(cancelRun).toHaveBeenCalledWith('run1')

    wrapper.unmount()
  })
})

describe('接回在跑的任务', () => {
  it('进页面先问活跃 run：有就接管进度', async () => {
    vi.mocked(fetchActiveRuns).mockResolvedValue([
      run({ oid: 'run9', conversation_oid: 'agent1', status: 'running', rounds: 3 })
    ])
    const wrapper = mountPage()
    await settle()

    expect(fetchActiveRuns).toHaveBeenCalled()
    expect(wrapper.text()).toContain('ai.agent.run.background')
    expect(wrapper.text()).toContain('ai.agent.run.roundsDone')

    await vi.advanceTimersByTimeAsync(1500)
    await settle()
    expect(fetchRunDetail).toHaveBeenCalledWith('run9', 0)

    wrapper.unmount()
  })
})

describe('回复渲染', () => {
  it('助手回复走 markdown，用户气泡保持纯文本', async () => {
    vi.mocked(listMessages).mockResolvedValue({
      messages: [
        {
          oid: 'm1',
          seq: 1,
          role: 'user',
          content: '**别当 markdown**',
          created_at: '2026-10-25T00:00:00'
        },
        {
          oid: 'm2',
          seq: 2,
          role: 'assistant',
          content: '| 项目 | 数量 |\n| --- | --- |\n| 活动 | 3 |',
          created_at: '2026-10-25T00:01:00'
        }
      ],
      has_more: false,
      total: 2
    })
    const wrapper = mountPage()
    await settle()
    // markdown 渲染有 150ms 防抖，等它落定。
    await vi.advanceTimersByTimeAsync(200)
    await settle()

    // 助手回复：表格被渲染出来了。
    const markdown = wrapper.find('.ai-chat__markdown')
    expect(markdown.exists()).toBe(true)
    expect(markdown.find('table').exists()).toBe(true)
    expect(markdown.find('td').text()).toBe('活动')

    // 用户气泡：还是纯文本，没被当成 markdown 渲染。
    const userText = wrapper.find('.ai-chat__row.user .ai-chat__text')
    expect(userText.exists()).toBe(true)
    expect(userText.find('strong').exists()).toBe(false)
    expect(userText.text()).toContain('**别当 markdown**')

    wrapper.unmount()
  })
})

describe('待确认提案', () => {
  it('进页面拉一次待确认提案', async () => {
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid: '7'.repeat(32), tool_name: 'db_insert' })]
    })
    const wrapper = mountPage()
    await settle()

    expect(fetchPendingProposals).toHaveBeenCalledWith('proposed')
    expect(wrapper.text()).toContain('ai.agent.pending.count')

    wrapper.unmount()
  })

  it('提案能直接确认（页面刷新后走这条路）', async () => {
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid: '6'.repeat(32) })]
    })
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      result_summary: '{"ok":true}',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.agent.pending.entry')!.trigger('click')
    await settle()
    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await settle()

    expect(confirmProposal).toHaveBeenCalledWith('6'.repeat(32))
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')

    wrapper.unmount()
  })
})

describe('首次使用风险警示', () => {
  it('首次进入智能体：先弹警示，确认前不建会话、不切会话', async () => {
    localStorage.clear()
    const wrapper = mountPage()
    await settle()

    const dialog = wrapper.find('.ai-agent__risk-dialog')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('ai.agent.risk.power')
    expect(dialog.text()).toContain('ai.agent.risk.ban')
    expect(dialog.text()).toContain('ai.agent.risk.audit')

    // 门禁没放行：既没建会话，也没把最近的会话选进来
    expect(createConversation).not.toHaveBeenCalled()
    expect(listMessages).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('点「我已知悉」：写标记、关弹窗、正常进入，再进不再弹', async () => {
    localStorage.clear()
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.agent.risk.ack')!.trigger('click')
    await settle()
    await wrapper.vm.$nextTick()

    expect(localStorage.getItem(RISK_KEY)).toBe('1')
    expect(wrapper.find('.ai-agent__risk-dialog').exists()).toBe(false)
    // 门禁放行后接回最近会话
    expect(listMessages).toHaveBeenCalledWith('agent1', expect.objectContaining({ afterSeq: 0 }))
    wrapper.unmount()

    // 同一账号再进：标记还在，不弹
    const again = mountPage()
    await settle()
    expect(again.find('.ai-agent__risk-dialog').exists()).toBe(false)
    again.unmount()
  })

  it('点取消：不写标记、不建会话、不提交，下次动作还能再弹', async () => {
    localStorage.clear()
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.agent.risk.cancel')!.trigger('click')
    await settle()
    await wrapper.vm.$nextTick()

    expect(localStorage.getItem(RISK_KEY)).toBeNull()
    expect(wrapper.find('.ai-agent__risk-dialog').exists()).toBe(false)
    expect(createConversation).not.toHaveBeenCalled()
    expect(submitRun).not.toHaveBeenCalled()

    // 取消后点「新建会话」：门禁重新弹框，依然不建
    await findButton(wrapper, 'ai.chat.sessions.new')!.trigger('click')
    await settle()
    expect(wrapper.find('.ai-agent__risk-dialog').exists()).toBe(true)
    expect(createConversation).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('已有本账号标记：进页面不弹', async () => {
    const wrapper = mountPage()
    await settle()

    expect(wrapper.find('.ai-agent__risk-dialog').exists()).toBe(false)
    expect(listMessages).toHaveBeenCalledWith('agent1', expect.objectContaining({ afterSeq: 0 }))

    wrapper.unmount()
  })

  it('普通对话模式：不弹，也不拦建会话', async () => {
    localStorage.clear()
    const wrapper = mountPage({ mode: 'chat' })
    await settle()

    expect(wrapper.find('.ai-agent__risk-dialog').exists()).toBe(false)

    await findButton(wrapper, 'ai.chat.sessions.new')!.trigger('click')
    await settle()

    expect(createConversation).toHaveBeenCalledWith('agent')

    wrapper.unmount()
  })
})

describe('双人动态码', () => {
  it('需要动态码的提案：先弹授权框，拿到码再带码确认', async () => {
    authOpenSpy.mockResolvedValue('123456')
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid: 'e'.repeat(32), tool_name: 'fs_write', requires_dual_auth: true })]
    })
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.agent.pending.entry')!.trigger('click')
    await settle()

    // 卡上常驻「需双人动态码」标签
    expect(wrapper.text()).toContain('ai.agent.dualAuth.badge')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await settle()

    expect(authOpenSpy).toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenCalledWith('e'.repeat(32), '123456')
    expect(authSucceedSpy).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('不需要动态码的提案：不弹授权框', async () => {
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid: 'f'.repeat(32) })]
    })
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await settle()

    await findButton(wrapper, 'ai.agent.pending.entry')!.trigger('click')
    await settle()
    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await settle()

    expect(authOpenSpy).not.toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenCalledWith('f'.repeat(32))

    wrapper.unmount()
  })
})

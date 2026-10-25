import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiChat from './ZAiChat.vue'
import { buildChatRequest } from './chatSession'
import {
  confirmProposal,
  createChatCompletion,
  fetchPendingProposals,
  fetchRun,
  listModels,
  rejectProposal,
  runAgent,
  streamChatCompletion
} from '@/api/ai'
import { AiRequestError } from '@/api/ai/errors'
import type { AiAgentProposal, AiAgentRunResponse } from '@/../types/ai'

const { elMessageSuccessSpy, authOpenSpy, authSucceedSpy, authFailSpy } = vi.hoisted(() => ({
  elMessageSuccessSpy: vi.fn(),
  authOpenSpy: vi.fn(),
  authSucceedSpy: vi.fn(),
  authFailSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listModels: vi.fn(),
    streamChatCompletion: vi.fn(),
    createChatCompletion: vi.fn(),
    runAgent: vi.fn(),
    confirmProposal: vi.fn(),
    rejectProposal: vi.fn(),
    fetchPendingProposals: vi.fn(),
    fetchRun: vi.fn()
  }
})

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
  const { defineComponent, h } = await import('vue')

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

  const ElSegmented = defineComponent({
    name: 'ElSegmented',
    props: {
      modelValue: { type: [String, Number, Boolean], default: '' },
      options: { type: Array, default: () => [] },
      disabled: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h(
          'div',
          { class: 'el-segmented-stub' },
          (props.options as Array<{ label: string; value: string }>).map((option) =>
            h(
              'button',
              {
                class: 'el-segmented-stub__item',
                'data-active': props.modelValue === option.value ? 'true' : 'false',
                onClick: () => {
                  if (!props.disabled) {
                    emit('update:modelValue', option.value)
                  }
                }
              },
              option.label
            )
          )
        )
    }
  })

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElButton,
    ElInput,
    ElSelect,
    ElSwitch,
    ElSegmented,
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
    ElTooltip: defineComponent({
      name: 'ElTooltip',
      setup(_, { slots }) {
        return () => h('div', { class: 'el-tooltip-stub' }, slots.default?.())
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
      error: vi.fn(),
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    })
  }
})

function mountPage(): VueWrapper {
  return mount(ZAiChat)
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

beforeEach(() => {
  vi.mocked(listModels).mockReset()
  vi.mocked(streamChatCompletion).mockReset()
  vi.mocked(createChatCompletion).mockReset()
  vi.mocked(runAgent).mockReset()
  vi.mocked(confirmProposal).mockReset()
  vi.mocked(rejectProposal).mockReset()
  vi.mocked(fetchPendingProposals).mockReset()
  vi.mocked(fetchRun).mockReset()
  authOpenSpy.mockReset()
  authSucceedSpy.mockReset()
  authFailSpy.mockReset()
  vi.mocked(listModels).mockResolvedValue({
    object: 'list',
    data: [{ id: 'gpt-4o-mini', object: 'model', owned_by: 'shared' }]
  })
  vi.mocked(fetchPendingProposals).mockResolvedValue({ proposals: [] })
  vi.mocked(fetchRun).mockResolvedValue({ request_id: 'req-1', events: [] })
})

/** 后端 expires_at 的形态：本地时间、空格分隔、不带时区。 */
function serverTime(offsetMs: number): string {
  const at = new Date(Date.now() + offsetMs)
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  )
}

/** run 的响应骨架：新契约多了 stop_reason / rounds / usage。 */
function runResponse(overrides: Partial<AiAgentRunResponse> = {}): AiAgentRunResponse {
  return {
    request_id: 'req-1',
    content: '',
    stop_reason: null,
    rounds: 1,
    usage: null,
    events: [],
    proposals: [],
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

async function switchToAgent(wrapper: VueWrapper) {
  const item = wrapper
    .findAll('.el-segmented-stub__item')
    .find((button) => button.text() === 'ai.chat.modes.agent')
  expect(item).toBeTruthy()
  await item!.trigger('click')
}

async function sendText(wrapper: VueWrapper, text: string) {
  await wrapper.find('.el-input-stub').setValue(text)
  await findButton(wrapper, 'ai.chat.send')!.trigger('click')
  await flushPromises()
}

describe('请求体形状', () => {
  it('只带 model、messages 与 stream', () => {
    const request = buildChatRequest([{ role: 'user', content: '你好' }], 'gpt-4o-mini', true)
    expect(Object.keys(request).sort()).toEqual(['messages', 'model', 'stream'])
    expect(request).toEqual({
      model: 'gpt-4o-mini',
      messages: [{ role: 'user', content: '你好' }],
      stream: true
    })
  })

  it('空内容不进历史', () => {
    const request = buildChatRequest(
      [
        { role: 'user', content: '你好' },
        { role: 'assistant', content: '   ' }
      ],
      'gpt-4o-mini',
      false
    )
    expect(request.messages).toEqual([{ role: 'user', content: '你好' }])
    expect(request.stream).toBe(false)
  })
})

describe('对话页', () => {
  it('没有可用模型时给出引导文案', async () => {
    vi.mocked(listModels).mockResolvedValue({ object: 'list', data: [] })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-empty-stub').text()).toBe('ai.chat.modelsEmpty')
    expect(wrapper.text()).toContain('ai.chat.modelsEmpty')

    wrapper.unmount()
  })

  it('发送时按 model、messages、stream 调用网关', async () => {
    vi.mocked(streamChatCompletion).mockResolvedValue({
      text: '答案',
      done: true,
      interrupted: false,
      requestId: 'req-1',
      error: null
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-input-stub').setValue('你好')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(streamChatCompletion).toHaveBeenCalledWith(
      expect.objectContaining({
        model: 'gpt-4o-mini',
        messages: [{ role: 'user', content: '你好' }],
        // 审计场景名：后端落 AI_CALL_LOGS.PURPOSE（不影响额度）。
        scene: 'chat'
      })
    )
    expect(wrapper.text()).toContain('答案')

    wrapper.unmount()
  })

  it('关闭流式时走非流式请求', async () => {
    vi.mocked(createChatCompletion).mockResolvedValue({
      choices: [{ message: { content: '一次性答案' } }]
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-switch-stub').trigger('click')
    await wrapper.find('.el-input-stub').setValue('你好')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(createChatCompletion).toHaveBeenCalledWith(
      'gpt-4o-mini',
      [{ role: 'user', content: '你好' }],
      // 审计场景名：后端落 AI_CALL_LOGS.PURPOSE（不影响额度）。
      'chat'
    )
    expect(wrapper.text()).toContain('一次性答案')

    wrapper.unmount()
  })

  it('错误码展示人话说明与错误码，并按 Retry-After 倒计时', async () => {
    vi.mocked(streamChatCompletion).mockRejectedValue(
      new AiRequestError({
        status: 429,
        code: 'AI_RATE_LIMITED',
        retryAfter: 30,
        requestId: 'req-429'
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-input-stub').setValue('你好')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.errors.AI_RATE_LIMITED.title')
    expect(wrapper.text()).toContain('ai.errors.AI_RATE_LIMITED.hint')
    expect(wrapper.text()).toContain('AI_RATE_LIMITED')
    expect(wrapper.text()).toContain('req-429')
    expect(wrapper.text()).toContain('ai.chat.rateLimited')

    wrapper.unmount()
  })

  it('额度耗尽时展示已用量与上限', async () => {
    vi.mocked(streamChatCompletion).mockRejectedValue(
      new AiRequestError({
        status: 429,
        code: 'insufficient_quota',
        quotaUsed: 12,
        quotaLimit: 20
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-input-stub').setValue('你好')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.errors.INSUFFICIENT_QUOTA.title')
    expect(wrapper.text()).toContain('ai.chat.quotaUsed')

    wrapper.unmount()
  })

  it('没收到结束标记时标记为响应中断', async () => {
    vi.mocked(streamChatCompletion).mockResolvedValue({
      text: '半截回答',
      done: false,
      interrupted: true,
      requestId: 'req-2',
      error: null
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-input-stub').setValue('你好')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.chat.interrupted')
    expect(wrapper.text()).toContain('半截回答')

    wrapper.unmount()
  })

  it('模型列表加载失败时给出提示', async () => {
    vi.mocked(listModels).mockRejectedValue(new Error('boom'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('ai.chat.modelsLoadFailed')

    wrapper.unmount()
  })
})

describe('智能体模式', () => {
  it('切到智能体后走 /ai/agent/run，不走流式', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({ request_id: 'req-agent', content: '图书馆志愿有 2 个。' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下图书馆志愿')

    expect(runAgent).toHaveBeenCalledWith(
      [{ role: 'user', content: '查一下图书馆志愿' }],
      'gpt-4o-mini',
      expect.any(AbortSignal)
    )
    expect(streamChatCompletion).not.toHaveBeenCalled()
    expect(createChatCompletion).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('图书馆志愿有 2 个。')

    wrapper.unmount()
  })

  it('运行中显示「正在执行工具」而不是打字机', async () => {
    let release: (value: AiAgentRunResponse) => void = () => {}
    vi.mocked(runAgent).mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve
        })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await wrapper.find('.el-input-stub').setValue('查一下')
    await findButton(wrapper, 'ai.chat.send')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.agent.running')
    expect(wrapper.find('.ai-agent__pulse').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('ai.chat.thinking')

    release(runResponse({ request_id: 'req-1', content: '好了' }))
    await flushPromises()

    expect(wrapper.text()).not.toContain('ai.agent.running')
    expect(wrapper.text()).toContain('好了')

    wrapper.unmount()
  })

  it('工具时间线：一行一条，点开才显示结果', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        request_id: 'req-2',
        content: '已完成。',
        events: [
          {
            round_no: 1,
            tool_name: 'search_activities',
            tool_kind: 'read',
            args_summary: '{"query":"图书馆"}',
            result_summary: '找到 2 个活动',
            status: 'executed'
          },
          {
            round_no: 2,
            tool_name: 'update_member_duration',
            tool_kind: 'write',
            args_summary: '{"duration":6}',
            result_summary: '等待确认',
            status: 'proposed'
          },
          {
            round_no: 3,
            tool_name: 'not_a_tool',
            tool_kind: null,
            args_summary: null,
            result_summary: '工具不在白名单内，已拒绝',
            status: 'denied'
          }
        ]
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下并改时长')

    const rows = wrapper.findAll('.ai-agent__event-row')
    expect(rows).toHaveLength(3)
    // 工具名走中文标签；未知工具回落，不把原始名字直接丢给用户。
    expect(wrapper.text()).toContain('ai.agent.tools.search_activities')
    expect(wrapper.text()).toContain('ai.agent.tools.update_member_duration')
    expect(wrapper.text()).toContain('ai.agent.tools.unknown')
    // 状态徽标
    expect(wrapper.text()).toContain('ai.agent.status.executed')
    expect(wrapper.text()).toContain('ai.agent.status.proposed')
    expect(wrapper.text()).toContain('ai.agent.status.denied')
    // 只读 / 写 用不同色点区分
    expect(wrapper.find('.ai-agent__dot.is-write').exists()).toBe(true)
    expect(wrapper.find('.ai-agent__dot.is-read').exists()).toBe(true)
    // 写操作还有文字标签，不靠颜色单独传达
    expect(wrapper.text()).toContain('ai.agent.kindWrite')
    // 结果默认收起
    expect(wrapper.text()).not.toContain('找到 2 个活动')
    await rows[0]!.trigger('click')
    expect(wrapper.text()).toContain('找到 2 个活动')
    expect(rows[0]!.attributes('aria-expanded')).toBe('true')

    wrapper.unmount()
  })

  it('提案卡：确认执行后显示结果摘要', async () => {
    const oid = 'a'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        request_id: 'req-3',
        content: '已生成提案，请确认。',
        events: [
          {
            round_no: 1,
            tool_name: 'update_member_duration',
            tool_kind: 'write',
            args_summary: '{"duration":6}',
            result_summary: '等待确认',
            status: 'proposed'
          }
        ],
        proposals: [proposal({ oid })]
      })
    )
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      result_summary: '{"duration":6,"ok":true}',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '把张三时长改成 6 小时')

    expect(wrapper.text()).toContain('ai.agent.proposal.validFor')
    expect(wrapper.text()).toContain('ai.agent.tools.update_member_duration')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(confirmProposal).toHaveBeenCalledWith(oid)
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')
    expect(wrapper.text()).toContain('{"duration":6,"ok":true}')
    // 终态后不再给按钮
    expect(findButton(wrapper, 'ai.agent.proposal.confirm')).toBeUndefined()

    wrapper.unmount()
  })

  it('提案卡：驳回调 reject，标已驳回', async () => {
    const oid = 'b'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        request_id: 'req-4',
        proposals: [proposal({ oid, tool_name: 'set_member_status' })]
      })
    )
    vi.mocked(rejectProposal).mockResolvedValue({
      status: 'rejected',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '把报名改成已生效')

    await findButton(wrapper, 'ai.agent.proposal.reject')!.trigger('click')
    await flushPromises()

    expect(rejectProposal).toHaveBeenCalledWith(oid)
    expect(wrapper.text()).toContain('ai.agent.proposal.rejected')

    wrapper.unmount()
  })

  it('提案已被处理（409）时给人话与请求编号', async () => {
    const oid = 'c'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({ request_id: 'req-5', proposals: [proposal({ oid })] })
    )
    vi.mocked(confirmProposal).mockRejectedValue(
      new AiRequestError({
        status: 409,
        code: 'AI_AGENT_PROPOSAL_CONFLICT',
        requestId: 'req-409'
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改时长')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.errors.AI_AGENT_PROPOSAL_CONFLICT.title')
    expect(wrapper.text()).toContain('ai.errors.AI_AGENT_PROPOSAL_CONFLICT.hint')
    expect(wrapper.text()).toContain('AI_AGENT_PROPOSAL_CONFLICT')
    expect(wrapper.text()).toContain('req-409')

    wrapper.unmount()
  })

  it('提案已过期（410）落过期终态', async () => {
    const oid = 'd'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({ request_id: 'req-6', proposals: [proposal({ oid })] })
    )
    vi.mocked(confirmProposal).mockRejectedValue(
      new AiRequestError({
        status: 410,
        code: 'AI_AGENT_PROPOSAL_EXPIRED',
        requestId: 'req-410'
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改时长')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.agent.proposal.expired')
    expect(findButton(wrapper, 'ai.agent.proposal.confirm')).toBeUndefined()

    wrapper.unmount()
  })

  it('智能体请求失败沿用错误码与请求编号展示', async () => {
    vi.mocked(runAgent).mockRejectedValue(
      new AiRequestError({
        status: 403,
        code: 'AI_FORBIDDEN',
        requestId: 'req-403'
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下')

    expect(wrapper.text()).toContain('ai.errors.AI_FORBIDDEN.title')
    expect(wrapper.text()).toContain('req-403')

    wrapper.unmount()
  })
})

describe('结构化预览', () => {
  it('diff 逐行着色：新增绿、删除红、块头单独一类', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        content: '要改这个文件。',
        proposals: [
          proposal({
            tool_name: 'fs_write',
            preview: {
              kind: 'diff',
              path: '/etc/x.conf',
              diff: '@@ -1,2 +1,2 @@\n keep\n-old\n+new'
            }
          })
        ]
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下配置')

    expect(wrapper.text()).toContain('ai.agent.preview.diff')
    expect(wrapper.find('.ai-preview__line.is-add').text()).toBe('+new')
    expect(wrapper.find('.ai-preview__line.is-remove').text()).toBe('-old')
    expect(wrapper.find('.ai-preview__line.is-hunk').exists()).toBe(true)
    // text() 会 trim 掉上下文行的前导空格（原始文本带空格，见 agentSession 的用例）
    expect(wrapper.find('.ai-preview__line.is-context').text()).toBe('keep')

    wrapper.unmount()
  })

  it('远程命令进等宽可滚动区', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [
          proposal({
            tool_name: 'ssh_exec',
            preview: {
              kind: 'command',
              target: 'root@127.0.0.1:22',
              command: 'systemctl status nginx'
            }
          })
        ]
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '看下 nginx')

    expect(wrapper.text()).toContain('ai.agent.preview.command')
    expect(wrapper.find('.ai-preview__mono').text()).toContain('systemctl status nginx')
    expect(wrapper.text()).toContain('root@127.0.0.1:22')

    wrapper.unmount()
  })

  it('行变更给键值表', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [
          proposal({
            tool_name: 'db_update',
            preview: {
              kind: 'row_update',
              table: 'USERS',
              key: { OID: 'abc' },
              values: { NAME: '张三' }
            }
          })
        ]
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下名字')

    expect(wrapper.text()).toContain('ai.agent.preview.row_update')
    expect(wrapper.findAll('.yg-descriptions__item')).toHaveLength(2)
    expect(wrapper.text()).toContain('USERS')
    expect(wrapper.text()).toContain('张三')

    wrapper.unmount()
  })

  it('预览不认识时退回参数摘要，不渲染半截数据', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [proposal({ preview: { kind: 'quantum' } as never })]
      })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下')

    expect(wrapper.find('.ai-preview').exists()).toBe(false)
    expect(wrapper.text()).toContain('{"duration":6}')

    wrapper.unmount()
  })
})

describe('双人动态码', () => {
  it('需要动态码的提案：先弹授权框，拿到码再带码确认', async () => {
    const oid = 'e'.repeat(32)
    authOpenSpy.mockResolvedValue('123456')
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [proposal({ oid, tool_name: 'fs_write', requires_dual_auth: true })]
      })
    )
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下配置')

    // 卡上常驻「需双人动态码」标签
    expect(wrapper.text()).toContain('ai.agent.dualAuth.badge')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(authOpenSpy).toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenCalledWith(oid, '123456')
    expect(authSucceedSpy).toHaveBeenCalled()
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')

    wrapper.unmount()
  })

  it('不需要动态码的提案：不弹授权框', async () => {
    const oid = 'f'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(runResponse({ proposals: [proposal({ oid })] }))
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改时长')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(authOpenSpy).not.toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenCalledWith(oid)

    wrapper.unmount()
  })

  it('命中 30 分钟授权窗口：不弹授权框，也不传动态码', async () => {
    const oid = '5'.repeat(32)
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [
          proposal({
            oid,
            tool_name: 'fs_write',
            requires_dual_auth: true,
            dual_auth_cached: true
          })
        ]
      })
    )
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: true
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下配置')

    // 缓存命中：提示从「需双人动态码」换成中性的一句
    expect(wrapper.text()).toContain('ai.agent.dualAuth.cached')
    expect(wrapper.text()).not.toContain('ai.agent.dualAuth.badge')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(authOpenSpy).not.toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenCalledWith(oid)
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')

    wrapper.unmount()
  })

  it('窗口刚过期（422）：重新弹码，且不显示成失败', async () => {
    const oid = '4'.repeat(32)
    authOpenSpy.mockResolvedValue('123456')
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [
          proposal({
            oid,
            tool_name: 'fs_write',
            requires_dual_auth: true,
            dual_auth_cached: true
          })
        ]
      })
    )
    vi.mocked(confirmProposal)
      .mockRejectedValueOnce(new AiRequestError({ status: 422, code: 'AI_AGENT_TOTP_REQUIRED' }))
      .mockResolvedValueOnce({ status: 'confirmed', dual_auth_cached: true })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下配置')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    // 第一次没带码（命中缓存），服务端说要码 → 重新弹一次并带上码重试
    expect(confirmProposal).toHaveBeenNthCalledWith(1, oid)
    expect(authOpenSpy).toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenNthCalledWith(2, oid, '123456')
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')
    // 不该被显示成失败
    expect(wrapper.text()).not.toContain('ai.agent.proposal.error')

    wrapper.unmount()
  })

  it('首次授权成功后，同列表其余待确认的运维写卡片免码', async () => {
    const first = '3'.repeat(32)
    const second = '2'.repeat(32)
    authOpenSpy.mockResolvedValue('123456')
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [
          proposal({
            oid: first,
            tool_name: 'fs_write',
            requires_dual_auth: true,
            dual_auth_cached: false
          }),
          proposal({
            oid: second,
            tool_name: 'db_insert',
            requires_dual_auth: true,
            dual_auth_cached: false
          })
        ]
      })
    )
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改配置并插一行')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(confirmProposal).toHaveBeenCalledWith(first, '123456')
    // 另一张卡进入免码态：不再显示「需双人动态码」，改显示中性提示
    expect(wrapper.text()).toContain('ai.agent.dualAuth.cached')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(confirmProposal).toHaveBeenLastCalledWith(second)
    expect(authOpenSpy).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('动态码被拒（403）：弹层说明原因，卡片留在待确认可重试', async () => {
    const oid = '9'.repeat(32)
    authOpenSpy.mockResolvedValue('000000')
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({
        proposals: [proposal({ oid, tool_name: 'fs_write', requires_dual_auth: true })]
      })
    )
    vi.mocked(confirmProposal).mockRejectedValue(
      new AiRequestError({ status: 403, code: 'AI_AGENT_DUAL_AUTH_REJECTED' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改一下配置')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(authFailSpy).toHaveBeenCalled()
    expect(wrapper.text()).toContain('ai.agent.proposalErrors.forbidden')
    // 动态码类失败可以重试：确认按钮还在
    expect(findButton(wrapper, 'ai.agent.proposal.confirm')).toBeTruthy()
    // 最多试三次，不把人锁在弹层里
    expect(confirmProposal).toHaveBeenCalledTimes(3)

    wrapper.unmount()
  })

  it('缺动态码（422）：重新弹码要一次，不显示成失败', async () => {
    const oid = '8'.repeat(32)
    authOpenSpy.mockResolvedValue('123456')
    vi.mocked(runAgent).mockResolvedValue(runResponse({ proposals: [proposal({ oid })] }))
    vi.mocked(confirmProposal)
      .mockRejectedValueOnce(new AiRequestError({ status: 422, code: 'AI_AGENT_TOTP_REQUIRED' }))
      .mockResolvedValueOnce({ status: 'confirmed', dual_auth_cached: false })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '改时长')

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(authOpenSpy).toHaveBeenCalled()
    expect(confirmProposal).toHaveBeenLastCalledWith(oid, '123456')
    expect(wrapper.text()).toContain('ai.agent.proposal.confirmed')
    // 仍需二次验证不是失败：不该出现错误块
    expect(wrapper.text()).not.toContain('ai.agent.proposal.error')

    wrapper.unmount()
  })
})

describe('待确认提案入口', () => {
  it('进入智能体模式时拉一次待确认提案，展开能看到', async () => {
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid: '7'.repeat(32), tool_name: 'db_insert' })]
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await flushPromises()

    expect(fetchPendingProposals).toHaveBeenCalledWith('proposed')
    expect(wrapper.text()).toContain('ai.agent.pending.title')
    expect(wrapper.text()).toContain('ai.agent.pending.count')

    await findButton(wrapper, 'ai.agent.pending.entry')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.agent.tools.db_insert')

    wrapper.unmount()
  })

  it('没有待确认提案时给空态文案', async () => {
    vi.mocked(fetchPendingProposals).mockResolvedValue({ proposals: [] })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('ai.agent.pending.empty')

    wrapper.unmount()
  })

  it('待确认提案能直接确认（页面刷新后走这条路）', async () => {
    const oid = '6'.repeat(32)
    vi.mocked(fetchPendingProposals).mockResolvedValue({
      proposals: [proposal({ oid })]
    })
    vi.mocked(confirmProposal).mockResolvedValue({
      status: 'confirmed',
      dual_auth_cached: false
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await flushPromises()
    await findButton(wrapper, 'ai.agent.pending.entry')!.trigger('click')
    await flushPromises()

    await findButton(wrapper, 'ai.agent.proposal.confirm')!.trigger('click')
    await flushPromises()

    expect(confirmProposal).toHaveBeenCalledWith(oid)

    wrapper.unmount()
  })
})

describe('run 的终止原因', () => {
  it('超时用提示条说明，不当错误弹窗', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({ content: '查到一半', stop_reason: 'timeout', rounds: 3 })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下')

    expect(wrapper.find('.yg-alert--warning').exists()).toBe(true)
    expect(wrapper.text()).toContain('ai.agent.stopReason.timeout')
    expect(wrapper.text()).toContain('ai.agent.stopReason.timeoutHint')
    expect(wrapper.text()).not.toContain('ai.agent.proposal.error')

    wrapper.unmount()
  })

  it('token 预算与最大轮数各给一句说明', async () => {
    vi.mocked(runAgent).mockResolvedValue(
      runResponse({ content: '部分结果', stop_reason: 'token_budget' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下')

    expect(wrapper.text()).toContain('ai.agent.stopReason.token_budget')

    wrapper.unmount()
  })

  it('正常结束时不给提示条', async () => {
    vi.mocked(runAgent).mockResolvedValue(runResponse({ content: '好了' }))
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '查一下')

    expect(wrapper.find('.yg-alert--warning').exists()).toBe(false)

    wrapper.unmount()
  })
})

describe('回看一次运行', () => {
  it('点「回看」按 request_id 取回事件时间线', async () => {
    vi.mocked(runAgent).mockResolvedValue(runResponse({ request_id: 'req-9', content: '好了' }))
    vi.mocked(fetchRun).mockResolvedValue({
      request_id: 'req-9',
      events: [
        {
          round_no: 1,
          tool_name: 'fs_list',
          tool_kind: 'read',
          args_summary: null,
          result_summary: '3 个文件',
          status: 'executed'
        }
      ]
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchToAgent(wrapper)
    await sendText(wrapper, '看下目录')

    await findButton(wrapper, 'ai.agent.run.replay')!.trigger('click')
    await flushPromises()

    expect(fetchRun).toHaveBeenCalledWith('req-9')
    expect(wrapper.text()).toContain('ai.agent.tools.fs_list')

    wrapper.unmount()
  })
})

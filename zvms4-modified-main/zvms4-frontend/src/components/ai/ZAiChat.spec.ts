import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiChat from './ZAiChat.vue'
import { buildChatRequest } from './chatSession'
import { createChatCompletion, listModels, streamChatCompletion } from '@/api/ai'
import { AiRequestError } from '@/api/ai/errors'

const { elMessageSuccessSpy } = vi.hoisted(() => ({ elMessageSuccessSpy: vi.fn() }))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listModels: vi.fn(),
    streamChatCompletion: vi.fn(),
    createChatCompletion: vi.fn()
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

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElButton,
    ElInput,
    ElSelect,
    ElSwitch,
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
  vi.mocked(listModels).mockResolvedValue({
    object: 'list',
    data: [{ id: 'gpt-4o-mini', object: 'model', owned_by: 'shared' }]
  })
})

describe('请求体形状', () => {
  it('只带 model、messages 与 stream', () => {
    const request = buildChatRequest(
      [{ role: 'user', content: '你好' }],
      'gpt-4o-mini',
      true
    )
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
        messages: [{ role: 'user', content: '你好' }]
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

    expect(createChatCompletion).toHaveBeenCalledWith('gpt-4o-mini', [
      { role: 'user', content: '你好' }
    ])
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

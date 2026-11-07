import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZMarkdownAiDialog from './ZMarkdownAiDialog.vue'
import { listModels } from '@/api/ai/chat'
import { streamChatCompletion, type AiStreamOptions } from '@/api/ai/stream'

/**
 * AI 写作助手弹窗。
 *
 * 守住流式渲染的节奏：生成过程中预览按节流刷新（不逐字重渲染），
 * 结束后完整渲染一次，最终文本一字不差。
 */

vi.mock('@/api/ai/chat', () => ({
  listModels: vi.fn()
}))

vi.mock('@/api/ai/stream', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai/stream')>()
  return {
    ...actual,
    streamChatCompletion: vi.fn()
  }
})

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key })
  }
})

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, size: String },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          { class: 'el-button-stub', 'data-type': props.type, onClick: () => emit('click') },
          slots.default?.()
        )
    }
  })

  const ElButtonGroup = defineComponent({
    name: 'ElButtonGroup',
    setup(_, { slots }) {
      return () => h('div', { class: 'el-button-group-stub' }, slots.default?.())
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [slots.default?.(), slots.footer?.()])
          : null
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: String, default: '' },
      type: String,
      readonly: Boolean
    },
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

  const ElRadioGroup = defineComponent({
    name: 'ElRadioGroup',
    props: { modelValue: String },
    emits: ['update:modelValue'],
    setup(_, { slots }) {
      return () => h('div', { class: 'el-radio-group-stub' }, slots.default?.())
    }
  })

  const ElRadioButton = defineComponent({
    name: 'ElRadioButton',
    props: { value: String, label: String },
    setup(_, { slots }) {
      return () => h('button', { class: 'el-radio-stub' }, slots.default?.())
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: String },
    emits: ['update:modelValue'],
    setup(_, { slots }) {
      return () => h('div', { class: 'el-select-stub' }, slots.default?.())
    }
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: String },
    setup() {
      return () => null
    }
  })

  const ElAlert = defineComponent({
    name: 'ElAlert',
    props: { title: String, description: String },
    setup(props) {
      return () => h('div', { class: 'el-alert-stub' }, props.title)
    }
  })

  return {
    ElAlert,
    ElButton,
    ElButtonGroup,
    ElDialog,
    ElInput,
    ElOption,
    ElRadioButton,
    ElRadioGroup,
    ElSelect,
    ElMessage: Object.assign(vi.fn(), {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
      info: vi.fn()
    })
  }
})

/** 假定时器下的「等一切落定」。 */
async function settle(times = 8): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await vi.advanceTimersByTimeAsync(0)
  }
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

async function openDialog(): Promise<VueWrapper> {
  const wrapper = mount(ZMarkdownAiDialog, {
    props: { visible: false, source: '原文', hasSelection: false }
  })
  await wrapper.setProps({ visible: true })
  await settle()
  return wrapper
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(listModels).mockReset()
  vi.mocked(streamChatCompletion).mockReset()
  vi.mocked(listModels).mockResolvedValue({
    object: 'list',
    data: [{ id: 'gpt-4o-mini', object: 'model', owned_by: 'shared' }]
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('AI 写作助手', () => {
  it('流式期间节流渲染，结束后文本完整', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.mocked(streamChatCompletion).mockImplementation(async (options: AiStreamOptions) => {
      options.onDelta?.('第一段')
      options.onDelta?.('第二段')
      await gate
      return {
        text: '第一段第二段',
        done: true,
        interrupted: false,
        error: null,
        requestId: null
      }
    })

    const wrapper = await openDialog()
    await findButton(wrapper, 'markdown.editor.ai.run')!.trigger('click')
    await settle()

    // 生成过程中：预览已经出来了，但只到第一段（第二段被节流挡在后面）。
    const preview = wrapper.find('.yg-markdown-ai__preview')
    expect(preview.exists()).toBe(true)
    expect(preview.text()).toContain('第一段')
    expect(preview.text()).not.toContain('第二段')

    release()
    await settle()

    // 结束：完整渲染一次，最终文本一字不差。
    expect(wrapper.find('.yg-markdown-ai__preview').text()).toContain('第一段第二段')

    wrapper.unmount()
  })

  it('可以切回原文继续改', async () => {
    const wrapper = await openDialog()
    vi.mocked(streamChatCompletion).mockImplementation(async (options: AiStreamOptions) => {
      options.onDelta?.('改好的正文')
      return {
        text: '改好的正文',
        done: true,
        interrupted: false,
        error: null,
        requestId: null
      }
    })

    await findButton(wrapper, 'markdown.editor.ai.run')!.trigger('click')
    await settle()

    // 默认看渲染结果；切到「编辑」就是可改的原文。
    expect(wrapper.find('.yg-markdown-ai__preview').exists()).toBe(true)
    await findButton(wrapper, 'markdown.editor.edit')!.trigger('click')
    await settle()

    const textarea = wrapper.find('.yg-markdown-ai__section .el-input-stub')
    expect(textarea.exists()).toBe(true)
    expect((textarea.element as HTMLTextAreaElement).value).toBe('改好的正文')

    wrapper.unmount()
  })
})

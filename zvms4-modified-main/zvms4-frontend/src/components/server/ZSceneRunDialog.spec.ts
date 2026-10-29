import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ZSceneRunDialog from './ZSceneRunDialog.vue'
import { listModels } from '@/api/ai/chat.ts'
import { submitSceneRun } from '@/api/ai/scenes.ts'
import { fetchRunDetail, listMessages } from '@/api/ai/conversations.ts'

const { elMessageSpy, elementComponents } = vi.hoisted(() => {
  const ElButton = {
    name: 'ElButton',
    props: { disabled: Boolean, loading: Boolean, type: String, size: String },
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
  }
  const ElDialog = {
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    template:
      '<div v-if="modelValue" class="el-dialog-stub"><slot /><slot name="footer" /></div>'
  }
  const ElInput = {
    name: 'ElInput',
    props: { modelValue: String },
    emits: ['update:modelValue'],
    template:
      '<textarea class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
  const ElAlert = {
    name: 'ElAlert',
    props: { title: String },
    template: '<div class="el-alert-stub">{{ title }}</div>'
  }
  return {
    elMessageSpy: vi.fn(),
    elementComponents: { ElButton, ElDialog, ElInput, ElAlert }
  }
})

vi.mock('element-plus', () => ({
  ...elementComponents,
  ElMessage: Object.assign(elMessageSpy, {
    success: elMessageSpy,
    error: elMessageSpy,
    warning: elMessageSpy,
    info: elMessageSpy,
    closeAll: vi.fn()
  }),
  ElMessageBox: { confirm: vi.fn() }
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('@/api/ai/chat.ts', () => ({
  listModels: vi.fn()
}))

vi.mock('@/api/ai/scenes.ts', () => ({
  SCENE_CONTENT_LIMIT: 4000,
  SCENE_CONTEXT_LIMIT: 8000,
  submitSceneRun: vi.fn()
}))

vi.mock('@/api/ai/conversations.ts', () => ({
  fetchRunDetail: vi.fn(),
  listMessages: vi.fn()
}))

// 选择器自身的行为在自己的测试里钉；这里只验证「弹窗有没有把 model / thinking 送出去」
vi.mock('@/components/ai/ZAiModelThinkingPicker.vue', () => ({
  default: {
    name: 'ZAiModelThinkingPickerStub',
    props: {
      models: { type: Array, default: () => [] },
      model: { type: String, default: '' },
      thinking: { type: String, default: '' }
    },
    emits: ['update:model', 'update:thinking'],
    template: '<div class="picker-stub" />'
  }
}))

function mountDialog(attrs: Record<string, unknown> = {}): VueWrapper {
  return mount(ZSceneRunDialog, {
    props: {
      modelValue: false,
      scene: 'file_edit',
      title: 'AI 分析',
      context: 'listen 8080'
    },
    attrs
  })
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === text)
  if (!button) throw new Error(`button "${text}" not rendered`)
  return button
}

async function openAndRun(wrapper: VueWrapper, instruction = '看看这段配置'): Promise<void> {
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  await wrapper.findAll('.el-input-stub')[0].setValue(instruction)
  await buttonByText(wrapper, 'server.aiScene.submit').trigger('click')
  await flushPromises()
}

const ANSWER = '配置里的监听端口写错了'

beforeEach(() => {
  vi.mocked(listModels).mockReset()
  vi.mocked(listModels).mockResolvedValue({
    object: 'list',
    data: [{ id: 'model-a', object: 'model', owned_by: 'test' }]
  } as never)
  vi.mocked(submitSceneRun).mockReset()
  vi.mocked(submitSceneRun).mockResolvedValue({
    run_id: 'run-1',
    conversation_oid: 'conv-1'
  } as never)
  vi.mocked(fetchRunDetail).mockReset()
  vi.mocked(fetchRunDetail).mockResolvedValue({
    run: { oid: 'run-1', conversation_oid: 'conv-1', status: 'succeeded' },
    events: [],
    proposals: [],
    messages: [{ oid: 'm2', seq: 2, role: 'assistant', content: ANSWER, created_at: '' }]
  } as never)
  vi.mocked(listMessages).mockReset()
  elMessageSpy.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('forwards the picked model and thinking level with the run', async () => {
  const wrapper = mountDialog()
  await wrapper.setProps({ modelValue: true })
  await flushPromises()

  expect(listModels).toHaveBeenCalledTimes(1)
  const picker = wrapper.findComponent({ name: 'ZAiModelThinkingPickerStub' })
  expect(picker.exists()).toBe(true)
  picker.vm.$emit('update:model', 'model-b')
  picker.vm.$emit('update:thinking', 'high')
  await flushPromises()

  await wrapper.findAll('.el-input-stub')[0].setValue('看看这段配置')
  await buttonByText(wrapper, 'server.aiScene.submit').trigger('click')
  await flushPromises()

  expect(submitSceneRun).toHaveBeenCalledWith('file_edit', '看看这段配置', 'listen 8080', {
    model: 'model-b',
    thinking: 'high'
  })
  expect(wrapper.find('.scene-run__result').text()).toContain(ANSWER)

  wrapper.unmount()
})

it('keeps the picker hidden when the model list cannot be loaded', async () => {
  vi.mocked(listModels).mockRejectedValue(new Error('no models'))
  const wrapper = mountDialog()
  await wrapper.setProps({ modelValue: true })
  await flushPromises()

  expect(wrapper.find('.picker-stub').exists()).toBe(false)
  expect(wrapper.find('.scene-run__body').exists()).toBe(true)

  wrapper.unmount()
})

it('emits apply so the host page can write the result back', async () => {
  const onApply = vi.fn()
  const wrapper = mountDialog({ onApply })
  await openAndRun(wrapper)

  await buttonByText(wrapper, 'server.aiScene.apply').trigger('click')

  expect(onApply).toHaveBeenCalledTimes(1)
  expect(onApply.mock.calls[0]![0]).toEqual({ text: ANSWER })

  wrapper.unmount()
})

it('hides the apply action when nobody listens to it', async () => {
  const wrapper = mountDialog()
  await openAndRun(wrapper)

  expect(wrapper.findAll('button').some((item) => item.text() === 'server.aiScene.apply')).toBe(
    false
  )
  expect(
    wrapper.findAll('button').some((item) => item.text() === 'server.aiScene.download')
  ).toBe(true)

  wrapper.unmount()
})

it('downloads the result as a local markdown blob', async () => {
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:scene')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(() => undefined)

  const wrapper = mountDialog()
  await openAndRun(wrapper)

  await buttonByText(wrapper, 'server.aiScene.download').trigger('click')
  await flushPromises()

  expect(createObjectURL).toHaveBeenCalledTimes(1)
  const blob = createObjectURL.mock.calls[0]![0] as unknown as Blob
  expect(blob).toBeInstanceOf(Blob)
  expect(blob.type).toContain('text/markdown')
  const text = await new Promise<string>((resolve) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.readAsText(blob)
  })
  expect(text).toBe(ANSWER)

  expect(click).toHaveBeenCalledTimes(1)
  const anchor = (click.mock.instances as unknown as HTMLAnchorElement[])[0]!
  expect(anchor.download).toMatch(/^scene-\d{8}-\d{6}\.md$/)

  wrapper.unmount()
})

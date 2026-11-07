import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h } from 'vue'
import ZFilePreview from './ZFilePreview.vue'
import { previewFile } from '@/api/system/files.ts'
import { ocrFile, submitSceneRun } from '@/api/ai/scenes.ts'
import { listModels } from '@/api/ai/chat.ts'
import { fetchRunDetail } from '@/api/ai/conversations.ts'
import { AiRequestError } from '@/api/ai/errors.ts'

const { elMessageSpy, applyContentSpy } = vi.hoisted(() => ({
  elMessageSpy: vi.fn(),
  applyContentSpy: vi.fn()
}))

const elementComponents = vi.hoisted(() => {
  const ElDialog = {
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    template:
      '<div v-if="modelValue" class="el-dialog-stub" :data-title="title"><slot /><slot name="footer" /></div>'
  }
  const ElButton = {
    name: 'ElButton',
    props: { disabled: Boolean, loading: Boolean, type: String, size: String },
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
  }
  const ElButtonGroup = { name: 'ElButtonGroup', template: '<div><slot /></div>' }
  const ElTag = { name: 'ElTag', template: '<span><slot /></span>' }
  const ElCheckbox = {
    name: 'ElCheckbox',
    props: { modelValue: Boolean },
    emits: ['update:modelValue'],
    template: '<label><slot /></label>'
  }
  const ElInput = {
    name: 'ElInput',
    props: { modelValue: String },
    emits: ['update:modelValue', 'keydown'],
    template:
      '<textarea class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
  const ElAlert = {
    name: 'ElAlert',
    props: { title: String },
    template: '<div class="el-alert-stub">{{ title }}</div>'
  }
  const ElResult = {
    name: 'ElResult',
    props: { title: String, subTitle: String },
    template: '<div class="el-result-stub">{{ title }}<slot name="extra" /></div>'
  }
  const ElImageViewer = { name: 'ElImageViewer', template: '<div />' }
  // 场景弹窗里的模型/思考选择器会用到；模型列表取不到时它不渲染，但导入不能是 undefined
  const ElSelect = { name: 'ElSelect', template: '<div class="el-select-stub" />' }
  const ElOption = { name: 'ElOption', template: '<div />' }
  const ElSegmented = { name: 'ElSegmented', template: '<div class="el-segmented-stub" />' }
  return {
    ElDialog,
    ElButton,
    ElButtonGroup,
    ElTag,
    ElCheckbox,
    ElInput,
    ElAlert,
    ElResult,
    ElImageViewer,
    ElSelect,
    ElOption,
    ElSegmented
  }
})

vi.mock('element-plus', () => ({
  ...elementComponents,
  ElLoadingDirective: { mounted: () => undefined, updated: () => undefined },
  ElMessage: Object.assign(elMessageSpy, {
    success: elMessageSpy,
    error: elMessageSpy,
    warning: elMessageSpy,
    info: elMessageSpy,
    closeAll: vi.fn()
  }),
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() }
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('@/api/system/files.ts', () => ({
  previewFile: vi.fn(),
  downloadFile: vi.fn(),
  editFile: vi.fn()
}))

vi.mock('@/api/ai/scenes.ts', () => ({
  SCENE_CONTENT_LIMIT: 4000,
  SCENE_CONTEXT_LIMIT: 8000,
  submitSceneRun: vi.fn(),
  ocrFile: vi.fn()
}))

vi.mock('@/api/ai/chat.ts', () => ({
  listModels: vi.fn()
}))

vi.mock('@/api/ai/conversations.ts', () => ({
  fetchRunDetail: vi.fn(),
  listMessages: vi.fn()
}))

vi.mock('@/components/markdown/ZMarkdown.vue', () => ({
  default: { name: 'ZMarkdownStub', template: '<div />' }
}))

// Monaco 在 jsdom 里跑不起来,文本分支用轻量 stub 顶替;只保留「AI 分析」入口这一处交互。
// ZFilePreview 用 defineAsyncComponent 拉 ZIde，需要 __esModule 让 Vue 走 default 互操作。
vi.mock('@/components/server/ZIde.vue', () => ({
  __esModule: true,
  default: {
    name: 'ZIdeStub',
    props: {
      content: String,
      encoding: String,
      editable: Boolean,
      readonlyReason: String,
      markdown: Boolean
    },
    emits: ['saved', 'analyze', 'close'],
    setup(
      props: { content?: string },
      { emit, expose }: { emit: (event: string, ...args: unknown[]) => void; expose: (api: Record<string, unknown>) => void }
    ) {
      expose({
        confirmLeave: async () => true,
        reset: () => undefined,
        isDirty: () => false,
        applyContent: applyContentSpy
      })
      return () =>
        h('div', { class: 'zide-stub' }, [
          h('pre', { class: 'zide-stub__content' }, props.content ?? ''),
          h(
            'button',
            {
              class: 'zide-stub__analyze',
              onClick: () => emit('analyze', props.content ?? '')
            },
            'server.files.ide.analyze'
          )
        ])
    }
  }
}))

const PATH = '/www/zvms4/data/sample.png'

function imagePreview(mime: string) {
  return {
    data: {
      status: 'ok',
      code: 200,
      data: { kind: 'image', mime, size: 1024, content: 'AAAA', truncated: false }
    }
  }
}

function textPreview(content: string) {
  return {
    data: {
      status: 'ok',
      code: 200,
      data: { kind: 'text', mime: 'text/plain', size: content.length, content, encoding: 'utf-8', truncated: false }
    }
  }
}

async function openPreview(name: string, payload: unknown): Promise<VueWrapper> {
  vi.mocked(previewFile).mockResolvedValue(payload as never)
  const wrapper = mount(ZFilePreview, {
    props: { modelValue: false, path: PATH, name },
    global: { directives: { loading: {} } }
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === text)
  if (!button) throw new Error(`button "${text}" not rendered`)
  return button
}

function hasButton(wrapper: VueWrapper, text: string): boolean {
  return wrapper.findAll('button').some((item) => item.text() === text)
}

beforeEach(() => {
  vi.mocked(previewFile).mockReset()
  vi.mocked(ocrFile).mockReset()
  vi.mocked(submitSceneRun).mockReset()
  vi.mocked(fetchRunDetail).mockReset()
  vi.mocked(listModels).mockReset()
  vi.mocked(listModels).mockRejectedValue(new Error('no models'))
  applyContentSpy.mockReset()
  applyContentSpy.mockResolvedValue(true)
  elMessageSpy.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('offers OCR for png images', async () => {
  const wrapper = await openPreview('sample.png', imagePreview('image/png'))

  expect(hasButton(wrapper, 'server.files.preview.ocr')).toBe(true)

  wrapper.unmount()
})

it('hides OCR for gif and svg images', async () => {
  const gif = await openPreview('anim.gif', imagePreview('image/gif'))
  expect(hasButton(gif, 'server.files.preview.ocr')).toBe(false)
  gif.unmount()

  const svg = await openPreview('icon.svg', imagePreview('image/svg+xml'))
  expect(hasButton(svg, 'server.files.preview.ocr')).toBe(false)
  svg.unmount()
})

it('renders the recognised text after clicking OCR', async () => {
  vi.mocked(ocrFile).mockResolvedValue({ text: '第一行\n第二行' } as never)
  const wrapper = await openPreview('sample.png', imagePreview('image/png'))

  await buttonByText(wrapper, 'server.files.preview.ocr').trigger('click')
  await flushPromises()

  expect(ocrFile).toHaveBeenCalledTimes(1)
  expect(ocrFile).toHaveBeenCalledWith(PATH)
  expect(wrapper.find('.ocr-result__text').text()).toContain('第一行')

  wrapper.unmount()
})

it('shows the permission copy when OCR is forbidden', async () => {
  vi.mocked(ocrFile).mockRejectedValue(new AiRequestError({ status: 403, code: 'AI_FORBIDDEN' }))
  const wrapper = await openPreview('sample.png', imagePreview('image/png'))

  await buttonByText(wrapper, 'server.files.preview.ocr').trigger('click')
  await flushPromises()

  expect(wrapper.find('.ocr-result__error').text()).toBe('server.files.preview.ocrForbidden')

  wrapper.unmount()
})

it('submits the editor content to the file_edit scene and shows the reply', async () => {
  vi.mocked(submitSceneRun).mockResolvedValue({
    run_id: 'run-1',
    conversation_oid: 'conv-1'
  } as never)
  vi.mocked(fetchRunDetail).mockResolvedValue({
    run: { oid: 'run-1', conversation_oid: 'conv-1', status: 'succeeded' },
    events: [],
    proposals: [],
    messages: [
      { oid: 'm1', seq: 1, role: 'user', content: '看看这段配置', created_at: '' },
      { oid: 'm2', seq: 2, role: 'assistant', content: '配置里的监听端口写错了', created_at: '' }
    ]
  } as never)

  const wrapper = await openPreview('app.conf', textPreview('listen 8080'))
  await wrapper.find('.zide-stub__analyze').trigger('click')
  await flushPromises()

  const dialog = wrapper.find('.scene-run__body')
  expect(dialog.exists()).toBe(true)
  expect(wrapper.text()).toContain('listen 8080')

  await dialog.findAll('.el-input-stub')[0].setValue('看看这段配置')
  await buttonByText(wrapper, 'server.aiScene.submit').trigger('click')
  await flushPromises()

  expect(submitSceneRun).toHaveBeenCalledTimes(1)
  expect(submitSceneRun).toHaveBeenCalledWith('file_edit', '看看这段配置', 'listen 8080', {
    model: null,
    thinking: null
  })
  expect(fetchRunDetail).toHaveBeenCalledWith('run-1', 0, 0)
  expect(wrapper.find('.scene-run__result').text()).toContain('监听端口写错了')

  wrapper.unmount()
})

it('writes the file_edit result back into the editor', async () => {
  vi.mocked(submitSceneRun).mockResolvedValue({
    run_id: 'run-2',
    conversation_oid: 'conv-2'
  } as never)
  vi.mocked(fetchRunDetail).mockResolvedValue({
    run: { oid: 'run-2', conversation_oid: 'conv-2', status: 'succeeded' },
    events: [],
    proposals: [],
    messages: [
      { oid: 'm3', seq: 2, role: 'assistant', content: 'listen 9090', created_at: '' }
    ]
  } as never)

  const wrapper = await openPreview('app.conf', textPreview('listen 8080'))
  await wrapper.find('.zide-stub__analyze').trigger('click')
  await flushPromises()

  await wrapper.findAll('.el-input-stub')[0].setValue('改成 9090')
  await buttonByText(wrapper, 'server.aiScene.submit').trigger('click')
  await flushPromises()

  await buttonByText(wrapper, 'server.aiScene.apply').trigger('click')
  await flushPromises()

  expect(applyContentSpy).toHaveBeenCalledTimes(1)
  expect(applyContentSpy).toHaveBeenCalledWith('listen 9090', 'replace')
  expect(elMessageSpy).toHaveBeenCalledWith('server.files.preview.aiApplied')

  wrapper.unmount()
})

// 弹层不关、直接换 path 时必须重拉；只看 modelValue 会把上一个文件的内容留在屏幕上
it('re-fetches when the path changes while the dialog stays open', async () => {
  vi.mocked(previewFile).mockResolvedValue(textPreview('listen 8080') as never)
  const wrapper = mount(ZFilePreview, {
    props: { modelValue: false, path: '/www/zvms4/a.conf', name: 'a.conf' },
    global: { directives: { loading: {} } }
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  expect(previewFile).toHaveBeenCalledTimes(1)

  await wrapper.setProps({ path: '/www/zvms4/b.conf', name: 'b.conf' })
  await flushPromises()

  expect(previewFile).toHaveBeenCalledTimes(2)
  expect(previewFile).toHaveBeenLastCalledWith('/www/zvms4/b.conf')

  wrapper.unmount()
})

// 换文件时旧请求慢回来，不能覆盖新文件的内容
it('ignores a stale preview response that lands after the newer one', async () => {
  let resolveFirst: (value: unknown) => void = () => undefined
  const first = new Promise((resolve) => {
    resolveFirst = resolve
  })
  vi.mocked(previewFile)
    .mockReturnValueOnce(first as never)
    .mockResolvedValueOnce(textPreview('NEW') as never)

  const wrapper = mount(ZFilePreview, {
    props: { modelValue: false, path: '/www/zvms4/old.conf', name: 'old.conf' },
    global: { directives: { loading: {} } }
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  await wrapper.setProps({ path: '/www/zvms4/new.conf', name: 'new.conf' })
  await flushPromises()

  resolveFirst(textPreview('OLD'))
  await flushPromises()

  expect(wrapper.find('.zide-stub__content').text()).toContain('NEW')
  expect(wrapper.find('.zide-stub__content').text()).not.toContain('OLD')

  wrapper.unmount()
})

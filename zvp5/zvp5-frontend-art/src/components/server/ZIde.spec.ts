import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const env = vi.hoisted(() => {
  const listeners: Array<() => void> = []
  const editorState = { value: '', readOnly: true, wordWrap: 'off' }
  const fakeEditor = {
    getValue: () => editorState.value,
    setValue: (next: string) => {
      editorState.value = next
      for (const listener of listeners) listener()
    },
    updateOptions: (options: Record<string, unknown>) => {
      if ('readOnly' in options) editorState.readOnly = Boolean(options.readOnly)
      if ('wordWrap' in options) editorState.wordWrap = String(options.wordWrap)
    },
    onDidChangeModelContent: (callback: () => void) => {
      listeners.push(callback)
      return { dispose: () => undefined }
    },
    onDidChangeCursorPosition: () => ({ dispose: () => undefined }),
    addCommand: () => undefined,
    focus: () => undefined,
    layout: () => undefined,
    dispose: () => undefined,
    getModel: () => ({ getLineCount: () => editorState.value.split('\n').length }),
    getSelection: () => null,
    // insert 模式把文本追加到末尾（stub 不做真实范围运算，够验证「不整体覆盖」）
    executeEdits: (_source: string, edits: Array<{ text: string }>) => {
      for (const edit of edits) editorState.value += edit.text
      for (const listener of listeners) listener()
    }
  }
  return {
    editorState,
    fakeEditor,
    // 模拟用户在 Monaco 里敲字:改值并触发内容变更回调
    type: (next: string) => {
      editorState.value = next
      for (const listener of listeners) listener()
    },
    resetEditor: () => {
      editorState.value = ''
      editorState.readOnly = true
      editorState.wordWrap = 'off'
      listeners.length = 0
    },
    elMessageSpy: vi.fn(),
    messageBoxConfirm: vi.fn()
  }
})

vi.mock('monaco-editor/editor', () => ({
  editor: {
    create: vi.fn((_host: unknown, options: { value: string }) => {
      env.editorState.value = options.value
      return env.fakeEditor
    }),
    setModelLanguage: vi.fn()
  },
  languages: {
    register: vi.fn(),
    setMonarchTokensProvider: vi.fn()
  },
  KeyMod: { CtrlCmd: 2048 },
  KeyCode: { KeyS: 49 }
}))

vi.mock('monaco-editor/features/register.all', () => ({}))

vi.mock('monaco-editor/editor/editor.worker?worker&url', () => ({ default: '/assets/editor.worker.js' }))

vi.mock('monaco-editor/languages/definitions/yaml/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/python/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/shell/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/javascript/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/typescript/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/html/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/css/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/sql/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/markdown/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/xml/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/ini/register.js', () => ({}))
vi.mock('monaco-editor/languages/definitions/dockerfile/register.js', () => ({}))

vi.mock('element-plus', () => {
  const ElButton = {
    name: 'ElButton',
    props: { disabled: Boolean, loading: Boolean, type: String, size: String },
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
  }
  const ElButtonGroup = { name: 'ElButtonGroup', template: '<div><slot /></div>' }
  const ElAlert = {
    name: 'ElAlert',
    props: { title: String },
    template: '<div class="el-alert-stub">{{ title }}</div>'
  }
  return {
    ElButton,
    ElButtonGroup,
    ElAlert,
    ElMessage: Object.assign(env.elMessageSpy, {
      success: env.elMessageSpy,
      error: env.elMessageSpy,
      warning: env.elMessageSpy,
      info: env.elMessageSpy,
      closeAll: vi.fn()
    }),
    ElMessageBox: { confirm: env.messageBoxConfirm }
  }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('@/api/system/files.ts', () => ({
  editFile: vi.fn()
}))

vi.mock('@/components/markdown/ZMarkdown.vue', () => ({
  default: {
    name: 'ZMarkdownStub',
    props: { source: String },
    template: '<div class="zmarkdown-stub">{{ source }}</div>'
  }
}))

const { editFile } = await import('@/api/system/files.ts')
const ZIde = (await import('./ZIde.vue')).default

const PATH = '/www/zvms4/app.conf'

type IdeProps = {
  path: string
  name: string
  content: string
  encoding: string
  editable: boolean
  readonlyReason: string
  markdown: boolean
}

function mountIde(overrides: Partial<IdeProps> = {}): VueWrapper {
  return mount(ZIde, {
    props: {
      path: PATH,
      name: 'app.conf',
      content: 'listen 8080',
      encoding: 'utf-8',
      editable: true,
      readonlyReason: '',
      markdown: false,
      ...overrides
    }
  })
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
  env.resetEditor()
  env.elMessageSpy.mockReset()
  env.messageBoxConfirm.mockReset()
  vi.mocked(editFile).mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('stays readonly when the file cannot be written back', async () => {
  const wrapper = mountIde({
    editable: false,
    readonlyReason: 'server.files.preview.editReadonlyEncoding'
  })

  expect(env.editorState.readOnly).toBe(true)
  const edit = buttonByText(wrapper, 'server.files.ide.edit')
  expect(edit.attributes('disabled')).toBeDefined()

  await edit.trigger('click')
  expect(env.editorState.readOnly).toBe(true)
  expect(hasButton(wrapper, 'server.files.ide.save')).toBe(false)
  expect(wrapper.find('.zide__note').text()).toBe('server.files.preview.editReadonlyEncoding')

  wrapper.unmount()
})

it('switches between preview and edit and unlocks the editor', async () => {
  const wrapper = mountIde()
  expect(env.editorState.readOnly).toBe(true)

  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')

  expect(env.editorState.readOnly).toBe(false)
  expect(hasButton(wrapper, 'server.files.ide.save')).toBe(true)
  expect(hasButton(wrapper, 'server.files.ide.cancel')).toBe(true)

  wrapper.unmount()
})

it('saves through the edit API and reports the backend payload', async () => {
  vi.mocked(editFile).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { path: PATH, size: 12, encoding: 'utf-8' } }
  } as never)

  const wrapper = mountIde()
  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')
  env.type('listen 9090')
  await flushPromises()

  await buttonByText(wrapper, 'server.files.ide.save').trigger('click')
  await flushPromises()

  expect(editFile).toHaveBeenCalledTimes(1)
  expect(editFile).toHaveBeenCalledWith(PATH, 'listen 9090', 'utf-8')
  expect(wrapper.emitted('saved')?.[0]?.[0]).toMatchObject({
    content: 'listen 9090',
    size: 12,
    encoding: 'utf-8'
  })
  expect(env.editorState.readOnly).toBe(true)
  expect(env.elMessageSpy).toHaveBeenCalledWith('server.files.ide.saved')

  wrapper.unmount()
})

it('asks before closing with unsaved changes', async () => {
  const wrapper = mountIde()
  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')
  env.type('listen 9090')
  await flushPromises()

  const close = wrapper.find('[aria-label="server.files.ide.close"]')

  env.messageBoxConfirm.mockRejectedValueOnce(new Error('cancel'))
  await close.trigger('click')
  await flushPromises()
  expect(wrapper.emitted('close')).toBeUndefined()

  env.messageBoxConfirm.mockResolvedValueOnce(undefined)
  await close.trigger('click')
  await flushPromises()
  expect(wrapper.emitted('close')).toHaveLength(1)

  wrapper.unmount()
})

it('toggles the rendered markdown preview for .md files', async () => {
  const wrapper = mountIde({ name: 'README.md', markdown: true, content: '# 标题' })

  expect(wrapper.find('.zmarkdown-stub').exists()).toBe(false)

  await buttonByText(wrapper, 'server.files.ide.rendered').trigger('click')
  expect(wrapper.find('.zmarkdown-stub').text()).toBe('# 标题')

  await buttonByText(wrapper, 'server.files.ide.source').trigger('click')
  expect(wrapper.find('.zmarkdown-stub').exists()).toBe(false)

  wrapper.unmount()
})

it('reports language, cursor and dirty state in the status bar', async () => {
  const wrapper = mountIde({ name: 'config.yaml', content: 'a\nb\nc' })

  const status = wrapper.find('.zide__status').text()
  expect(status).toContain('YAML')
  expect(status).toContain('utf-8')
  expect(status).toContain('server.files.ide.position')
  expect(status).toContain('server.files.ide.lines')
  expect(wrapper.text()).toContain('server.files.ide.stats')
  expect(wrapper.find('.zide__dirty').exists()).toBe(false)

  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')
  env.type('a\nb\nc\nd')
  await flushPromises()

  expect(wrapper.find('.zide__dirty').text()).toContain('server.files.ide.dirty')

  wrapper.unmount()
})

// ---------- applyContent：AI 结果写回 ----------
type ApplyApi = { applyContent: (text: string, mode?: 'replace' | 'insert') => Promise<boolean> }

function applyApi(wrapper: VueWrapper): ApplyApi {
  return wrapper.vm as unknown as ApplyApi
}

it('applies AI text over the whole file and switches to edit mode', async () => {
  const wrapper = mountIde()
  expect(env.editorState.readOnly).toBe(true)

  const applied = await applyApi(wrapper).applyContent('listen 9090\n', 'replace')
  await flushPromises()

  expect(applied).toBe(true)
  expect(env.editorState.value).toBe('listen 9090\n')
  expect(env.editorState.readOnly).toBe(false)
  expect(wrapper.find('.zide__dirty').exists()).toBe(true)
  expect(wrapper.text()).toContain('server.files.ide.stats')

  wrapper.unmount()
})

it('inserts AI text without dropping the existing content', async () => {
  const wrapper = mountIde()
  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')

  const applied = await applyApi(wrapper).applyContent('\n# tail', 'insert')
  await flushPromises()

  expect(applied).toBe(true)
  expect(env.editorState.value).toBe('listen 8080\n# tail')

  wrapper.unmount()
})

it('asks before replacing unsaved changes and keeps them when cancelled', async () => {
  const wrapper = mountIde()
  await buttonByText(wrapper, 'server.files.ide.edit').trigger('click')
  env.type('listen 9090')
  await flushPromises()

  env.messageBoxConfirm.mockRejectedValueOnce(new Error('cancel'))
  const cancelled = await applyApi(wrapper).applyContent('listen 7070', 'replace')
  await flushPromises()

  expect(env.messageBoxConfirm).toHaveBeenCalledTimes(1)
  expect(cancelled).toBe(false)
  expect(env.editorState.value).toBe('listen 9090')

  env.messageBoxConfirm.mockResolvedValueOnce(undefined)
  const applied = await applyApi(wrapper).applyContent('listen 7070', 'replace')
  await flushPromises()

  expect(applied).toBe(true)
  expect(env.editorState.value).toBe('listen 7070')

  wrapper.unmount()
})

it('refuses to apply into a file that cannot be written back', async () => {
  const wrapper = mountIde({ editable: false })

  const applied = await applyApi(wrapper).applyContent('listen 7070', 'replace')
  await flushPromises()

  expect(applied).toBe(false)
  expect(env.editorState.readOnly).toBe(true)
  expect(env.editorState.value).toBe('listen 8080')
  expect(env.elMessageSpy).toHaveBeenCalledWith('server.files.ide.applyReadonly')

  wrapper.unmount()
})

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Document, EditPen, MagicStick, Sort, View } from '@element-plus/icons-vue'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/editor/editor.all.js'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution'
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
import 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution'
import 'monaco-editor/esm/vs/basic-languages/html/html.contribution'
import 'monaco-editor/esm/vs/basic-languages/css/css.contribution'
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution'
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution'
import 'monaco-editor/esm/vs/basic-languages/xml/xml.contribution'
import 'monaco-editor/esm/vs/basic-languages/ini/ini.contribution'
import 'monaco-editor/esm/vs/basic-languages/dockerfile/dockerfile.contribution'
import { editFile } from '@/api/system/files.ts'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'

const MAX_EDIT_BYTES = 512 * 1024

// 可写回的编码:后端只放行 utf-8 与 gbk。latin-1 只是预览解码兜底,写回会破坏原始字节。
const EDIT_ENCODINGS = ['utf-8', 'gbk'] as const
type EditEncoding = (typeof EDIT_ENCODINGS)[number]

// 服务器上的常见文本类型;不在表里的一律回落 plaintext(不加载多余语言包)。
const EXT_LANGUAGE: Record<string, string> = {
  yaml: 'yaml',
  yml: 'yaml',
  json: 'json',
  py: 'python',
  sh: 'shell',
  bash: 'shell',
  zsh: 'shell',
  fish: 'shell',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  html: 'html',
  htm: 'html',
  vue: 'html',
  xml: 'xml',
  xsl: 'xml',
  css: 'css',
  scss: 'css',
  sass: 'css',
  less: 'css',
  sql: 'sql',
  md: 'markdown',
  markdown: 'markdown',
  toml: 'ini',
  ini: 'ini',
  cfg: 'ini',
  conf: 'ini',
  env: 'ini',
  properties: 'ini',
  editorconfig: 'ini',
  service: 'ini',
  timer: 'ini',
  dockerfile: 'dockerfile'
}

const LANGUAGE_LABELS: Record<string, string> = {
  yaml: 'YAML',
  json: 'JSON',
  python: 'Python',
  shell: 'Shell',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  html: 'HTML',
  css: 'CSS',
  sql: 'SQL',
  markdown: 'Markdown',
  xml: 'XML',
  ini: 'INI',
  dockerfile: 'Dockerfile',
  plaintext: 'Plain Text'
}

// worker 只走 editor 自带的那一个(语法高亮出自 basic-languages,不需要语言服务 worker)。
// CSP 是 script-src 'self':?worker 在生产构建里产出同源 js 资产,不是 blob/inline。
;(globalThis as unknown as { MonacoEnvironment: { getWorker: () => Worker } }).MonacoEnvironment = {
  getWorker: () => new EditorWorker()
}

// monaco 0.52 的 basic-languages 里没有 json(那是 language service 的地盘),
// 这里注册一个够用的 Monarch 分词器,避免为了高亮把 json worker 也拖进来。
let jsonRegistered = false
function registerJsonLanguage() {
  if (jsonRegistered) return
  jsonRegistered = true
  monaco.languages.register({ id: 'json', extensions: ['.json'] })
  monaco.languages.setMonarchTokensProvider('json', {
    tokenizer: {
      root: [
        [/\/\/.*$/, 'comment'],
        [/"(?:[^"\\]|\\.)*"(?=\s*:)/, 'string'],
        [/"(?:[^"\\]|\\.)*"/, 'string'],
        [/-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/, 'number'],
        [/\b(?:true|false|null)\b/, 'keyword'],
        [/[{}[\]]/, '@brackets'],
        [/[,:]/, 'delimiter']
      ]
    }
  } as monaco.languages.IMonarchLanguage)
}

const props = withDefaults(
  defineProps<{
    path: string
    name: string
    content: string
    encoding?: string
    editable?: boolean
    readonlyReason?: string
    markdown?: boolean
  }>(),
  { encoding: 'utf-8', editable: true, readonlyReason: '', markdown: false }
)

const emit = defineEmits<{
  (e: 'saved', payload: { content: string; size?: number; encoding: string }): void
  (e: 'analyze', content: string): void
  (e: 'close'): void
}>()

const { t } = useI18n()

const host = ref<HTMLElement | null>(null)
const editor = shallowRef<monaco.editor.IStandaloneCodeEditor | null>(null)
const value = ref(props.content)
const editing = ref(false)
const saving = ref(false)
const error = ref('')
const dirty = ref(false)
const wrap = ref(false)
const rendered = ref(false)
const cursor = ref({ line: 1, column: 1 })
const lineCount = ref(props.content.split('\n').length)
const editEncoding = ref<EditEncoding>('utf-8')

const fileEncoding = computed<EditEncoding>(() =>
  (props.encoding ?? '').toLowerCase() === 'gbk' ? 'gbk' : 'utf-8'
)

const extension = computed(() => {
  const lower = props.name.toLowerCase()
  if (lower === 'dockerfile') return 'dockerfile'
  const index = lower.lastIndexOf('.')
  return index > 0 ? lower.slice(index + 1) : ''
})

const language = computed(() => EXT_LANGUAGE[extension.value] ?? 'plaintext')
const languageLabel = computed(() => LANGUAGE_LABELS[language.value] ?? language.value)

// GBK 里 CJK / 全角字符占 2 字节,其余按 1 字节估(GBK 无对应编码的字符由后端拒收)
function isGbkWide(code: number): boolean {
  return (
    (code >= 0x3000 && code <= 0x303f) ||
    (code >= 0x3400 && code <= 0x4dbf) ||
    (code >= 0x4e00 && code <= 0x9fff) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xff01 && code <= 0xff60)
  )
}

function byteLengthOf(input: string, key: EditEncoding): number {
  if (key === 'utf-8') return new TextEncoder().encode(input).length
  let total = 0
  for (const char of input) {
    const code = char.codePointAt(0) ?? 0
    total += code < 0x80 || !isGbkWide(code) ? 1 : 2
  }
  return total
}

const byteLength = computed(() => byteLengthOf(value.value, editEncoding.value))
const tooLarge = computed(() => byteLength.value > MAX_EDIT_BYTES)
const statsLabel = computed(() => {
  const chars = value.value.length
  return editEncoding.value === 'gbk'
    ? t('server.files.ide.statsApprox', { chars, bytes: byteLength.value })
    : t('server.files.ide.stats', { chars, bytes: byteLength.value })
})

watch(fileEncoding, (enc) => {
  editEncoding.value = enc
}, { immediate: true })

watch(language, (next) => {
  const model = editor.value?.getModel()
  if (model) monaco.editor.setModelLanguage(model, next)
})

function syncFromEditor(instance: monaco.editor.IStandaloneCodeEditor) {
  value.value = instance.getValue()
  dirty.value = value.value !== props.content
  lineCount.value = value.value.split('\n').length
}

function createEditor() {
  if (!host.value) return
  const instance = monaco.editor.create(host.value, {
    value: props.content,
    language: language.value,
    theme: 'vs-dark',
    readOnly: !editing.value,
    automaticLayout: true,
    fontSize: 13,
    lineHeight: 20,
    fontFamily: "'SF Mono', SFMono-Regular, Menlo, Consolas, 'Liberation Mono', ui-monospace, monospace",
    minimap: { enabled: true, renderCharacters: false, maxColumn: 90 },
    scrollBeyondLastLine: false,
    smoothScrolling: true,
    renderLineHighlight: 'all',
    tabSize: 4,
    wordWrap: wrap.value ? 'on' : 'off',
    folding: true,
    padding: { top: 8, bottom: 8 },
    scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 }
  })
  editor.value = instance
  instance.onDidChangeModelContent(() => syncFromEditor(instance))
  instance.onDidChangeCursorPosition((event) => {
    cursor.value = { line: event.position.lineNumber, column: event.position.column }
  })
  // VSCode 肌肉记忆:Ctrl/Cmd + S 直接保存
  instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    void save()
  })
}

onMounted(() => {
  registerJsonLanguage()
  createEditor()
  void nextTick(() => editor.value?.layout())
})

onBeforeUnmount(() => {
  editor.value?.dispose()
  editor.value = null
})

// 同一实例切换文件:内容、语言、只读态一起归位
watch(
  () => props.name,
  () => {
    editing.value = false
    rendered.value = false
    error.value = ''
    dirty.value = false
    editEncoding.value = fileEncoding.value
    value.value = props.content
    const instance = editor.value
    if (!instance) return
    if (instance.getValue() !== props.content) instance.setValue(props.content)
    instance.updateOptions({ readOnly: true })
    const model = instance.getModel()
    if (model) monaco.editor.setModelLanguage(model, language.value)
    lineCount.value = props.content.split('\n').length
    void nextTick(() => instance.layout())
  }
)

// 保存成功后父组件用后端回传的 size / encoding 覆写 content,这里跟着落一次
watch(
  () => props.content,
  (next) => {
    if (next === value.value) return
    value.value = next
    lineCount.value = next.split('\n').length
    const instance = editor.value
    if (instance && instance.getValue() !== next) instance.setValue(next)
    if (!editing.value) dirty.value = false
  }
)

function startEdit() {
  if (!props.editable) return
  rendered.value = false
  editing.value = true
  error.value = ''
  editEncoding.value = fileEncoding.value
  dirty.value = value.value !== props.content
  const instance = editor.value
  instance?.updateOptions({ readOnly: false })
  void nextTick(() => {
    instance?.focus()
    instance?.layout()
  })
}

async function confirmLeave(): Promise<boolean> {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      t('server.files.ide.discardConfirm'),
      t('server.files.ide.discardTitle'),
      { type: 'warning' }
    )
    return true
  } catch {
    return false
  }
}

function revert() {
  editing.value = false
  error.value = ''
  dirty.value = false
  value.value = props.content
  lineCount.value = props.content.split('\n').length
  const instance = editor.value
  if (!instance) return
  if (instance.getValue() !== props.content) instance.setValue(props.content)
  instance.updateOptions({ readOnly: true })
}

async function cancelEdit() {
  if (!(await confirmLeave())) return
  revert()
}

async function requestClose() {
  if (!(await confirmLeave())) return
  emit('close')
}

function saveErrorMessage(caught: unknown): string {
  const err = caught as AxiosError<{ detail?: unknown }>
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) {
    return t('server.files.ide.saveFailedDetail', { detail })
  }
  return t('server.files.ide.saveFailed')
}

async function save() {
  if (!props.editable || !editing.value || saving.value) return
  if (tooLarge.value) {
    error.value = t('server.files.ide.tooLarge')
    return
  }
  saving.value = true
  error.value = ''
  try {
    const res = await editFile(props.path, value.value, editEncoding.value)
    const body = res.data?.data
    editing.value = false
    dirty.value = false
    editor.value?.updateOptions({ readOnly: true })
    emit('saved', {
      content: value.value,
      size: body?.size,
      encoding: body?.encoding ?? editEncoding.value
    })
    ElMessage.success(t('server.files.ide.saved'))
  } catch (caught) {
    // 留在编辑态让用户改完再存;原因走后端 detail(403 权限 / 404 不存在 / 413 超限)
    error.value = saveErrorMessage(caught)
  } finally {
    saving.value = false
  }
}

function toggleWrap() {
  wrap.value = !wrap.value
  editor.value?.updateOptions({ wordWrap: wrap.value ? 'on' : 'off' })
}

async function toggleRendered() {
  rendered.value = !rendered.value
  if (rendered.value) return
  await nextTick()
  editor.value?.layout()
}

function reset() {
  editing.value = false
  rendered.value = false
  error.value = ''
  dirty.value = false
  saving.value = false
  editEncoding.value = fileEncoding.value
  value.value = props.content
  lineCount.value = props.content.split('\n').length
  const instance = editor.value
  if (!instance) return
  if (instance.getValue() !== props.content) instance.setValue(props.content)
  instance.updateOptions({ readOnly: true })
}

defineExpose({ confirmLeave, reset, isDirty: () => dirty.value })
</script>

<template>
  <div class="zide">
    <div class="zide__bar">
      <div class="zide__identity">
        <span class="zide__dot" :class="{ 'is-dirty': dirty }" aria-hidden="true" />
        <span class="zide__name" :title="name">{{ name }}</span>
        <span class="zide__lang">{{ languageLabel }}</span>
        <span v-if="!editable" class="zide__badge">{{ t('server.files.ide.readonly') }}</span>
      </div>
      <div class="zide__actions">
        <ElButton
          v-if="markdown && !editing"
          text
          bg
          size="small"
          :icon="rendered ? Document : View"
          @click="toggleRendered"
        >
          {{ rendered ? t('server.files.ide.source') : t('server.files.ide.rendered') }}
        </ElButton>
        <ElButton text bg size="small" :icon="Sort" @click="toggleWrap">
          {{ wrap ? t('server.files.ide.noWrap') : t('server.files.ide.wrap') }}
        </ElButton>
        <ElButton text bg size="small" :icon="MagicStick" @click="emit('analyze', value)">
          {{ t('server.files.ide.analyze') }}
        </ElButton>
        <template v-if="editing">
          <ElButtonGroup size="small">
            <ElButton
              :type="editEncoding === 'utf-8' ? 'primary' : 'default'"
              :plain="editEncoding !== 'utf-8'"
              @click="editEncoding = 'utf-8'"
            >
              {{ t('server.files.preview.editEncodingUtf8') }}
            </ElButton>
            <ElButton
              :type="editEncoding === 'gbk' ? 'primary' : 'default'"
              :plain="editEncoding !== 'gbk'"
              @click="editEncoding = 'gbk'"
            >
              {{ t('server.files.preview.editEncodingGbk') }}
            </ElButton>
          </ElButtonGroup>
          <ElButton size="small" :disabled="saving" @click="cancelEdit">
            {{ t('server.files.ide.cancel') }}
          </ElButton>
          <ElButton
            type="primary"
            size="small"
            :icon="Check"
            :loading="saving"
            :disabled="saving || !dirty || tooLarge"
            @click="save"
          >
            {{ saving ? t('server.files.ide.saving') : t('server.files.ide.save') }}
          </ElButton>
        </template>
        <ElButton
          v-else
          size="small"
          :icon="EditPen"
          :disabled="!editable"
          :title="editable ? t('server.files.ide.edit') : readonlyReason"
          @click="startEdit"
        >
          {{ t('server.files.ide.edit') }}
        </ElButton>
        <ElButton
          text
          bg
          size="small"
          :icon="Close"
          :title="t('server.files.ide.close')"
          :aria-label="t('server.files.ide.close')"
          @click="requestClose"
        />
      </div>
    </div>

    <p v-if="readonlyReason" class="zide__note">{{ readonlyReason }}</p>

    <ElAlert
      v-if="error"
      class="zide__alert"
      type="error"
      :closable="false"
      :title="error"
    />
    <ElAlert
      v-else-if="editing && tooLarge"
      class="zide__alert"
      type="warning"
      :closable="false"
      :title="t('server.files.ide.tooLarge')"
    />

    <div class="zide__body">
      <div ref="host" class="zide__editor" :class="{ 'is-hidden': rendered }" />
      <div v-if="rendered" class="zide__rendered">
        <ZMarkdown :source="value" />
      </div>
    </div>

    <div class="zide__status">
      <span class="tabular-nums">{{
        t('server.files.ide.position', { line: cursor.line, column: cursor.column })
      }}</span>
      <span class="tabular-nums">{{ t('server.files.ide.lines', { count: lineCount }) }}</span>
      <span class="tabular-nums">{{ statsLabel }}</span>
      <span v-if="editing && tooLarge" class="zide__status-warn">
        {{ t('server.files.ide.limitHint') }}
      </span>
      <span class="zide__spacer" />
      <span>{{ languageLabel }}</span>
      <span class="zide__status-enc">{{ editEncoding }}</span>
      <span v-if="dirty" class="zide__dirty">● {{ t('server.files.ide.dirty') }}</span>
      <span v-else-if="!editable">{{ t('server.files.ide.readonly') }}</span>
    </div>
  </div>
</template>

<style scoped>
.zide {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface);
  overflow: hidden;
}

.zide__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
  padding: var(--yg-space-2) var(--yg-space-3);
  border-bottom: 1px solid var(--yg-border-subtle);
  background: var(--yg-bg-surface);
}

.zide__identity {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-width: 0;
}

.zide__dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: var(--yg-radius-pill);
  background: var(--yg-border-strong);
}

.zide__dot.is-dirty {
  background: var(--yg-color-warning);
}

.zide__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.zide__lang,
.zide__badge {
  flex: 0 0 auto;
  padding: 1px var(--yg-space-2);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-bg-surface-sunken);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.zide__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-1);
}

.zide__note {
  margin: 0;
  padding: var(--yg-space-1) var(--yg-space-3);
  border-bottom: 1px solid var(--yg-border-subtle);
  border-left: 2px solid var(--yg-color-warning);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-secondary);
}

.zide__alert {
  margin: var(--yg-space-2) var(--yg-space-3);
}

.zide__body {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

.zide__editor {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  /* vs-dark 的底色:避免 monaco 首帧画出来之前闪白 */
  background: #1e1e1e;
}

.zide__editor.is-hidden {
  display: none;
}

.zide__rendered {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: var(--yg-space-4);
  background: var(--yg-bg-surface);
}

.zide__status {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  padding: 0 var(--yg-space-3);
  height: 26px;
  border-top: 1px solid var(--yg-border-subtle);
  background: var(--yg-bg-surface-sunken);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
}

.zide__spacer {
  flex: 1 1 auto;
}

.zide__status-enc {
  font-family: var(--yg-font-mono);
}

.zide__status-warn {
  color: var(--yg-text-warning);
}

.zide__dirty {
  color: var(--yg-text-warning);
}
</style>

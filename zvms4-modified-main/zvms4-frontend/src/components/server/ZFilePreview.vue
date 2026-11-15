<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AxiosError } from 'axios'
import { ElImageViewer, ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Download, EditPen, Refresh } from '@element-plus/icons-vue'
import { previewFile, downloadFile, editFile, type FilePreviewData } from '@/api/system/files.ts'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'
import Skeleton from '@/ui/ux/Skeleton.vue'

const SMALL_TEXT_LIMIT = 200 * 1024
const MAX_ROWS = 5000
const MAX_EDIT_BYTES = 512 * 1024

// 可写回的编码:后端只放行 utf-8 与 gbk。latin-1 只是预览解码兜底(任何字节都能解出来),
// 拿它写回会把原始字节换成另一串字节,等于破坏文件,所以只读。
const EDIT_ENCODINGS = ['utf-8', 'gbk'] as const
type EditEncoding = (typeof EDIT_ENCODINGS)[number]

// 这些扩展名前端直接按 binary 呈现,不发 preview 请求
const BINARY_EXTENSIONS = new Set([
  'zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst', '7z', 'rar', 'iso', 'img', 'deb', 'rpm', 'apk',
  'jar', 'war', 'class', 'bin', 'o', 'a', 'so', 'dll', 'exe', 'dylib', 'pyc', 'wasm',
  'db', 'sqlite', 'sqlite3', 'mdb', 'dat',
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'woff', 'woff2', 'ttf', 'otf', 'eot'
])

const props = defineProps<{
  modelValue: boolean
  path: string
  name: string
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const loading = ref(false)
const failed = ref(false)
const failure = ref('')
const skipped = ref(false)
const data = ref<FilePreviewData | null>(null)
const sourceMode = ref<'rendered' | 'source'>('rendered')
const wrap = ref(false)
const lightbox = ref(false)
const dimensions = ref<{ w: number; h: number } | null>(null)

// —— 编辑态 ——
const editing = ref(false)
const draft = ref('')
const editEncoding = ref<EditEncoding>('utf-8')
const saving = ref(false)
const saveError = ref('')

const preview = computed(() => data.value)
const isText = computed(() => preview.value?.kind === 'text' && typeof preview.value.content === 'string')
const isImage = computed(() => preview.value?.kind === 'image' && typeof preview.value.content === 'string')
const text = computed(() => (isText.value ? (preview.value?.content ?? '') : ''))
const imageSrc = computed(() =>
  isImage.value ? `data:${preview.value?.mime || 'image/*'};base64,${preview.value?.content ?? ''}` : ''
)
const isMdFile = computed(() => props.name.toLowerCase().endsWith('.md'))
const showRendered = computed(() => isMdFile.value && sourceMode.value === 'rendered')
const large = computed(
  () => isText.value && (preview.value?.truncated || text.value.length > SMALL_TEXT_LIMIT)
)
const noPreview = computed(
  () => skipped.value || preview.value?.kind === 'binary' || preview.value?.kind === 'too_large'
)

const lines = computed(() => text.value.split('\n'))
const asRows = computed(() => isText.value && !large.value && lines.value.length <= MAX_ROWS)
const shownRows = computed(() => (asRows.value ? lines.value.slice(0, MAX_ROWS) : []))
const hiddenRows = computed(() =>
  asRows.value ? Math.max(0, lines.value.length - MAX_ROWS) : 0
)

const mimeLabel = computed(() => preview.value?.mime || '—')
const encodingLabel = computed(() =>
  editing.value ? editEncoding.value : preview.value?.encoding || '—'
)

// —— 编辑可用性判定 ——
// 1) 只对文本预览开放;2) 预览被截断时保存会覆盖文件尾部;3) latin-1 写回会破坏原始字节。
const fileEncoding = computed(() => (preview.value?.encoding ?? '').toLowerCase())
const truncated = computed(() => preview.value?.truncated === true)
const encodingWritable = computed(() =>
  (EDIT_ENCODINGS as readonly string[]).includes(fileEncoding.value)
)
const canEdit = computed(() => isText.value && !truncated.value && encodingWritable.value)
const readonlyReason = computed(() => {
  if (!isText.value) return ''
  if (truncated.value) return t('server.files.preview.editReadonlyTruncated')
  if (!encodingWritable.value) return t('server.files.preview.editReadonlyEncoding')
  return ''
})
const dirty = computed(() => editing.value && draft.value !== text.value)

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

function byteLengthOf(value: string, encoding: EditEncoding): number {
  if (encoding === 'utf-8') return new TextEncoder().encode(value).length
  let total = 0
  for (const char of value) {
    const code = char.codePointAt(0) ?? 0
    total += code < 0x80 || !isGbkWide(code) ? 1 : 2
  }
  return total
}

const editBytes = computed(() => byteLengthOf(draft.value, editEncoding.value))
const editStats = computed(() => {
  const chars = draft.value.length
  return editEncoding.value === 'gbk'
    ? t('server.files.preview.editStatsApprox', { chars, bytes: editBytes.value })
    : t('server.files.preview.editStats', { chars, bytes: editBytes.value })
})
const editTooLarge = computed(() => editBytes.value > MAX_EDIT_BYTES)

function fmtSize(size: number): string {
  if (size < 1024) return `${size} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = size
  let unit = 'B'
  for (const u of units) {
    if (value < 1024) break
    value /= 1024
    unit = u
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}`
}

const sizeLabel = computed(() =>
  skipped.value ? '—' : fmtSize(preview.value?.size ?? 0)
)

function failureMessage(error: unknown): string {
  const err = error as AxiosError<{ detail?: unknown }>
  const status = err?.response?.status ?? 0
  if (status === 403) return t('server.files.preview.forbidden')
  if (status === 404) return t('server.files.preview.notFound')
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return t('server.files.preview.loadFailed')
}

async function fetchPreview() {
  loading.value = true
  failed.value = false
  skipped.value = false
  failure.value = ''
  data.value = null
  sourceMode.value = 'rendered'
  dimensions.value = null
  resetEdit()
  if (props.name.includes('.') && BINARY_EXTENSIONS.has(props.name.split('.').pop()!.toLowerCase())) {
    skipped.value = true
    loading.value = false
    return
  }
  try {
    const res = await previewFile(props.path)
    const body = res.data?.data
    if (!body) throw new Error('empty preview payload')
    data.value = body
  } catch (error) {
    failed.value = true
    failure.value = failureMessage(error)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) void fetchPreview()
    else resetEdit()
  }
)

function onImageLoad(event: Event) {
  const img = event.target as HTMLImageElement
  dimensions.value = { w: img.naturalWidth, h: img.naturalHeight }
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(text.value)
    ElMessage.success(t('server.files.preview.copied'))
  } catch {
    ElMessage.error(t('server.files.preview.copyFailed'))
  }
}

async function download() {
  try {
    const res = await downloadFile(props.path)
    const url = URL.createObjectURL(res.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = props.name
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 3000)
  } catch {
    ElMessage.error(t('server.files.preview.downloadFailed'))
  }
}

// —— 编辑 —— //
function resetEdit() {
  editing.value = false
  draft.value = ''
  saveError.value = ''
  saving.value = false
}

function startEdit() {
  if (!canEdit.value) return
  editing.value = true
  draft.value = text.value
  editEncoding.value = fileEncoding.value === 'gbk' ? 'gbk' : 'utf-8'
  saveError.value = ''
  // Markdown 的渲染态与编辑态互斥:进编辑先落到源码视图,退出后仍能看源码。
  sourceMode.value = 'source'
}

async function confirmDiscard(): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      t('server.files.preview.editDiscardConfirm'),
      t('server.files.preview.editDiscardTitle'),
      { type: 'warning' }
    )
    return true
  } catch {
    return false
  }
}

async function cancelEdit() {
  if (dirty.value && !(await confirmDiscard())) return
  resetEdit()
}

// 关闭弹窗(X / ESC)时若还有未保存的修改,先问一句再放行
function onBeforeClose(done: () => void) {
  if (!dirty.value) {
    resetEdit()
    done()
    return
  }
  void confirmDiscard().then((ok) => {
    if (!ok) return
    resetEdit()
    done()
  })
}

function editErrorMessage(error: unknown): string {
  const err = error as AxiosError<{ detail?: unknown }>
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) {
    return t('server.files.preview.editSaveFailedDetail', { detail })
  }
  return t('server.files.preview.editSaveFailed')
}

// Tab 键插入制表符,而不是把焦点移走(代码文件的常见预期)
function onEditorKeydown(event: KeyboardEvent) {
  if (event.key !== 'Tab' || event.shiftKey) return
  const target = event.target
  if (!(target instanceof HTMLTextAreaElement)) return
  const start = target.selectionStart
  const end = target.selectionEnd
  draft.value = `${draft.value.slice(0, start)}\t${draft.value.slice(end)}`
  event.preventDefault()
  void nextTick(() => {
    target.selectionStart = start + 1
    target.selectionEnd = start + 1
  })
}

async function saveEdit() {
  if (!editing.value || saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    const res = await editFile(props.path, draft.value, editEncoding.value)
    const body = res.data?.data
    const current = data.value
    if (current) {
      // 用后端回传的 size / encoding 覆盖本地预览,保证标签与内容一致
      data.value = {
        ...current,
        content: draft.value,
        size: body?.size ?? current.size,
        encoding: body?.encoding ?? editEncoding.value
      }
    }
    editing.value = false
    draft.value = ''
    ElMessage.success(t('server.files.preview.editSaved'))
  } catch (error) {
    // 留在编辑态,让用户改完再存;原因走后端 detail(403 权限 / 404 不存在 / 413 超限)
    saveError.value = editErrorMessage(error)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('server.files.preview.title') + ' · ' + name"
    width="min(960px, 94vw)"
    :close-on-click-modal="false"
    :before-close="onBeforeClose"
  >
    <div class="meta-row">
      <ElTag size="small" effect="plain" class="font-mono">{{ mimeLabel }}</ElTag>
      <span class="yg-text-xs yg-text-tertiary tabular-nums">{{ sizeLabel }}</span>
      <span v-if="isText && !skipped" class="yg-text-xs yg-text-tertiary font-mono">
        {{ t('server.files.preview.encodingLabel') }}: {{ encodingLabel }}
      </span>
      <ElTag v-if="preview?.truncated" size="small" type="warning" effect="plain">
        {{ t('server.files.preview.truncatedHint') }}
      </ElTag>
      <span class="min-w-0 flex-1" />
      <template v-if="isText && !editing">
        <ElButtonGroup v-if="isMdFile && !skipped" size="small">
          <ElButton
            :type="sourceMode === 'rendered' ? 'primary' : 'default'"
            :plain="sourceMode !== 'rendered'"
            @click="sourceMode = 'rendered'"
          >
            {{ t('server.files.preview.rendered') }}
          </ElButton>
          <ElButton
            :type="sourceMode === 'source' ? 'primary' : 'default'"
            :plain="sourceMode !== 'source'"
            @click="sourceMode = 'source'"
          >
            {{ t('server.files.preview.source') }}
          </ElButton>
        </ElButtonGroup>
        <ElCheckbox v-model="wrap" size="small">{{ t('server.files.preview.wrap') }}</ElCheckbox>
      </template>
    </div>

    <div v-if="readonlyReason" class="readonly-note">
      {{ readonlyReason }}
    </div>

    <div class="preview-body">
      <Skeleton v-if="loading" :lines="10" />
      <template v-else>
        <div v-if="editing" class="editor">
          <div class="editor-toolbar">
            <span class="yg-text-xs yg-text-tertiary">{{ t('server.files.preview.editEncodingLabel') }}</span>
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
            <span class="yg-text-xs yg-text-tertiary">{{ t('server.files.preview.editEncodingHint') }}</span>
            <span class="min-w-0 flex-1" />
            <span class="yg-text-xs yg-text-tertiary tabular-nums">{{ editStats }}</span>
            <span class="yg-text-xs yg-text-tertiary">{{ t('server.files.preview.editLimitHint') }}</span>
          </div>
          <ElAlert
            v-if="saveError"
            type="error"
            :title="saveError"
            :closable="false"
          />
          <ElAlert
            v-if="editTooLarge"
            type="warning"
            :title="t('server.files.preview.editTooLarge')"
            :closable="false"
          />
          <ElInput
            v-model="draft"
            type="textarea"
            resize="none"
            class="editor-area font-mono"
            @keydown="onEditorKeydown"
          />
        </div>

        <template v-else>
          <div v-if="failed" class="preview-state">
            <ElResult icon="error" :title="t('server.files.preview.loadFailed')" :sub-title="failure">
              <template #extra>
                <ElButton type="primary" plain :icon="Refresh" @click="fetchPreview">
                  {{ t('server.files.preview.retry') }}
                </ElButton>
              </template>
            </ElResult>
          </div>

          <div v-else-if="!preview || noPreview" class="preview-state">
            <ElResult
              icon="info"
              :title="t('server.files.preview.notSupportTitle')"
              :sub-title="preview?.kind === 'too_large' ? t('server.files.preview.tooLargeHint') : t('server.files.preview.binaryHint')"
            >
              <template #extra>
                <ElButton type="primary" :icon="Download" @click="download">
                  {{ t('server.files.download') }}
                </ElButton>
              </template>
            </ElResult>
          </div>

          <div v-else-if="isImage" class="image-body">
            <img
              :src="imageSrc"
              :alt="name"
              class="preview-img"
              loading="lazy"
              @load="onImageLoad"
              @click="lightbox = true"
            />
            <div class="yg-text-xs yg-text-tertiary pt-2 text-center tabular-nums">
              <span v-if="dimensions">{{ dimensions.w }} × {{ dimensions.h }} px · </span>{{ sizeLabel }}
              <span>· {{ t('server.files.preview.zoomHint') }}</span>
            </div>
          </div>

          <div v-else-if="isText && showRendered" class="preview-scroll rendered">
            <ZMarkdown :source="text" />
          </div>

          <template v-else-if="isText">
            <div
              v-if="asRows"
              class="preview-scroll font-mono"
              :class="wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre'"
            >
              <div v-for="(line, index) in shownRows" :key="index" class="text-row">
                <span class="row-no">{{ index + 1 }}</span>
                <span class="row-text">{{ line === '' ? ' ' : line }}</span>
              </div>
              <div v-if="hiddenRows > 0" class="rows-tail">
                {{ t('server.files.preview.hiddenLines', { count: hiddenRows }) }}
              </div>
            </div>
            <pre
              v-else
              class="preview-scroll font-mono blob"
              :class="wrap ? 'whitespace-pre-wrap break-all' : ''"
            >{{ text }}</pre>
          </template>
        </template>
      </template>
    </div>

    <template #footer>
      <template v-if="editing">
        <ElButton @click="cancelEdit">{{ t('server.files.preview.editCancel') }}</ElButton>
        <ElButton type="primary" :loading="saving" :disabled="saving" @click="saveEdit">
          {{ saving ? t('server.files.preview.editSaving') : t('server.files.preview.editSave') }}
        </ElButton>
      </template>
      <template v-else>
        <ElButton v-if="isText" :icon="CopyDocument" @click="copyText">
          {{ t('server.files.preview.copy') }}
        </ElButton>
        <ElButton
          v-if="isText"
          :icon="EditPen"
          :disabled="!canEdit"
          @click="startEdit"
        >
          {{ t('server.files.preview.edit') }}
        </ElButton>
        <ElButton type="primary" :icon="Download" @click="download">
          {{ t('server.files.download') }}
        </ElButton>
      </template>
    </template>
  </ElDialog>

  <ElImageViewer
    v-if="lightbox"
    :url-list="[imageSrc]"
    teleported
    hide-on-click-modal
    @close="lightbox = false"
  />
</template>

<style scoped>
.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding-bottom: 10px;
}

/* 只读原因:贴左边发丝竖线,不做浮层(浮层在 ElDialog 里会被 z-index 压住) */
.readonly-note {
  margin-bottom: 8px;
  border-left: 2px solid var(--el-color-warning);
  padding: 2px 0 2px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--yg-text-secondary);
}

.editor {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  gap: 8px;
}

.editor-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}

.editor-area {
  flex: 1 1 auto;
  min-height: 0;
}

.editor-area :deep(.el-textarea__inner) {
  height: 100%;
  min-height: 260px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  line-height: 20px;
  tab-size: 4;
}

.preview-body {
  display: flex;
  flex-direction: column;
  min-height: 340px;
  max-height: 66vh;
}

.preview-state {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 2px 4px;
}

.rendered {
  padding: 4px 8px;
}

.image-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.preview-img {
  max-width: 100%;
  max-height: calc(66vh - 60px);
  object-fit: contain;
  cursor: zoom-in;
}

.text-row {
  display: flex;
  line-height: 20px;
}

.row-no {
  flex: 0 0 auto;
  width: 52px;
  padding-right: 10px;
  text-align: right;
  color: var(--yg-text-tertiary);
  user-select: none;
}

.row-text {
  min-width: 0;
}

.rows-tail {
  padding: 6px 0 2px 52px;
  color: var(--yg-text-tertiary);
  font-size: 12px;
}

.blob {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  white-space: pre;
  overflow: auto;
}

.blob.whitespace-pre-wrap {
  white-space: pre-wrap;
  overflow-x: hidden;
}</style>

<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AxiosError } from 'axios'
import { ElImageViewer } from 'element-plus/es/components/image-viewer/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { CopyDocument, Document, Download, Refresh } from '@element-plus/icons-vue'
import { previewFile, downloadFile, type FilePreviewData } from '@/api/system/files.ts'
import { ocrFile } from '@/api/ai/scenes.ts'
import { aiErrorCodeOf, aiErrorCopy, aiErrorOf } from '@/api/ai/errors.ts'
import ZSceneRunDialog from '@/components/server/ZSceneRunDialog.vue'

// 可写回的编码:后端只放行 utf-8 与 gbk。latin-1 只是预览解码兜底(任何字节都能解出来),
// 拿它写回会把原始字节换成另一串字节,等于破坏文件,所以只读。
const EDIT_ENCODINGS = ['utf-8', 'gbk'] as const
type EditEncoding = (typeof EDIT_ENCODINGS)[number]

// 后端 OCR 只吃这三种,按扩展名决定「识别文字」按钮显隐(gif/bmp/ico/svg 一律不给)
const OCR_EXTENSIONS = new Set(['png', 'jpg', 'webp'])

// 这些扩展名前端直接按 binary 呈现,不发 preview 请求
const BINARY_EXTENSIONS = new Set([
  'zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst', '7z', 'rar', 'iso', 'img', 'deb', 'rpm', 'apk',
  'jar', 'war', 'class', 'bin', 'o', 'a', 'so', 'dll', 'exe', 'dylib', 'pyc', 'wasm',
  'db', 'sqlite', 'sqlite3', 'mdb', 'dat',
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'woff', 'woff2', 'ttf', 'otf', 'eot'
])

// Z-IDE 是 Monaco 的宿主，只有打开文本预览才用得到：异步加载让 monaco 整块退出首屏。
// 首帧给一个转圈占位，避免全屏弹窗出现后空白闪一下。
const ZIdeLoading = defineComponent({
  name: 'ZIdeLoading',
  render: () => h('div', { class: 'zide-loading' }, h('span', { class: 'zide-loading__spinner' }))
})

const ZIde = defineAsyncComponent({
  loader: () => import('@/components/server/ZIde.vue'),
  loadingComponent: ZIdeLoading,
  delay: 120
})

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
const lightbox = ref(false)
const dimensions = ref<{ w: number; h: number } | null>(null)
// 预览请求序号：换文件/重试时只认最后一次请求的结果
let previewSeq = 0

// 文本文件整体交给 Z-IDE(全屏 Monaco);md 的「渲染预览」切换也在它内部。
// 异步组件拿不到 InstanceType,这里按 Z-IDE defineExpose 的契约显式声明句柄。
interface ZIdeHandle {
  confirmLeave: () => Promise<boolean>
  reset: () => void
  isDirty: () => boolean
  applyContent: (text: string, mode?: 'replace' | 'insert') => Promise<boolean>
}
const ideRef = ref<ZIdeHandle | null>(null)

const preview = computed(() => data.value)
const isText = computed(() => preview.value?.kind === 'text' && typeof preview.value.content === 'string')
// 文本文件走 Z-IDE:全屏弹窗 + Monaco
const ideMode = computed(() => isText.value)
const isImage = computed(() => preview.value?.kind === 'image' && typeof preview.value.content === 'string')
const text = computed(() => (isText.value ? (preview.value?.content ?? '') : ''))
const imageSrc = computed(() =>
  isImage.value ? `data:${preview.value?.mime || 'image/*'};base64,${preview.value?.content ?? ''}` : ''
)
const isMdFile = computed(() => props.name.toLowerCase().endsWith('.md'))

// —— OCR / 智能分析 ——
const ocrLoading = ref(false)
const ocrText = ref('')
const ocrError = ref('')
const analysisVisible = ref(false)
const analysisContext = ref('')

const fileExtension = computed(() => {
  const index = props.name.lastIndexOf('.')
  return index >= 0 ? props.name.slice(index + 1).toLowerCase() : ''
})
const canOcr = computed(() => isImage.value && OCR_EXTENSIONS.has(fileExtension.value))

function aiErrorMessage(error: unknown, fallback: string): string {
  const request = aiErrorOf(error)
  if (!request) return fallback
  if (request.status === 403) return t('server.files.preview.ocrForbidden')
  const code = aiErrorCodeOf(error)
  // 后端给的是中文一句话时原样展示,机器码才翻成人话
  if (code && /[\u4e00-\u9fff]/.test(code)) return code
  return aiErrorCopy(code, t).title || fallback
}

async function runOcr() {
  if (ocrLoading.value) return
  ocrLoading.value = true
  ocrError.value = ''
  ocrText.value = ''
  try {
    const data = await ocrFile(props.path)
    ocrText.value = (data?.text ?? '').trim()
    if (!ocrText.value) ocrError.value = t('server.files.preview.ocrEmpty')
  } catch (error) {
    ocrError.value = aiErrorMessage(error, t('server.files.preview.ocrFailed'))
  } finally {
    ocrLoading.value = false
  }
}

async function copyOcrText() {
  if (!ocrText.value) return
  try {
    await navigator.clipboard.writeText(ocrText.value)
    ElMessage.success(t('server.files.preview.copied'))
  } catch {
    ElMessage.error(t('server.files.preview.copyFailed'))
  }
}

const noPreview = computed(
  () => skipped.value || preview.value?.kind === 'binary' || preview.value?.kind === 'too_large'
)

const mimeLabel = computed(() => preview.value?.mime || '—')
const encodingLabel = computed(() => preview.value?.encoding || '—')

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
  // 请求序号：同弹层里换文件时，慢响应不得覆盖后发请求的数据
  const seq = ++previewSeq
  loading.value = true
  failed.value = false
  skipped.value = false
  failure.value = ''
  data.value = null
  dimensions.value = null
  ideRef.value?.reset()
  ocrLoading.value = false
  ocrText.value = ''
  ocrError.value = ''
  if (props.name.includes('.') && BINARY_EXTENSIONS.has(props.name.split('.').pop()!.toLowerCase())) {
    skipped.value = true
    loading.value = false
    return
  }
  try {
    const res = await previewFile(props.path)
    if (seq !== previewSeq) return
    const body = res.data?.data
    if (!body) throw new Error('empty preview payload')
    data.value = body
  } catch (error) {
    if (seq !== previewSeq) return
    failed.value = true
    failure.value = failureMessage(error)
  } finally {
    if (seq === previewSeq) loading.value = false
  }
}

watch(
  // path 也要看：弹层不关、同组件直接换文件时同样要重拉
  () => [props.modelValue, props.path] as const,
  ([open]) => {
    if (open) void fetchPreview()
    else {
      // 关闭时让在途响应作废，避免关掉后又被旧数据写回
      previewSeq += 1
      ideRef.value?.reset()
    }
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

// —— 编辑 / 保存回写 —— //
// 编辑态、dirty 与保存请求都由 Z-IDE 自持;这里只把后端回传的 size / encoding 落到预览数据上。
function onSaved(payload: { content: string; size?: number; encoding: string }) {
  const current = data.value
  if (!current) return
  data.value = {
    ...current,
    content: payload.content,
    size: payload.size ?? current.size,
    encoding: payload.encoding
  }
}

function openAnalysis(content: string) {
  analysisContext.value = content
  analysisVisible.value = true
}

// file_edit 的「写回编辑器」：交给 Z-IDE 落地（它负责切编辑态、刷新 dirty 与状态栏）。
// 这里只负责保持弹窗焦点在当前文件，并把「已写入」告诉用户。
async function onApplyResult(payload: { text: string }) {
  const ide = ideRef.value
  if (!ide) return
  const applied = await ide.applyContent(payload.text, 'replace')
  if (applied) ElMessage.success(t('server.files.preview.aiApplied'))
}

// 关闭弹窗(X / ESC)时若还有未保存的修改,先问一句再放行
async function onBeforeClose(done: () => void) {
  const ide = ideRef.value
  if (ide && !(await ide.confirmLeave())) return
  ide?.reset()
  done()
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('server.files.preview.title') + ' · ' + name"
    :width="ideMode ? undefined : 'min(960px, 94vw)'"
    :fullscreen="ideMode"
    :show-close="!ideMode"
    :close-on-click-modal="false"
    :before-close="onBeforeClose"
    :class="['file-preview-dialog', ideMode ? 'file-preview-dialog--ide' : '']"
  >
    <div v-if="!ideMode" class="meta-row">
      <ElTag size="small" effect="plain" class="font-mono">{{ mimeLabel }}</ElTag>
      <span class="yg-text-xs yg-text-tertiary tabular-nums">{{ sizeLabel }}</span>
      <span v-if="isText && !skipped" class="yg-text-xs yg-text-tertiary font-mono">
        {{ t('server.files.preview.encodingLabel') }}: {{ encodingLabel }}
      </span>
      <ElTag v-if="preview?.truncated" size="small" type="warning" effect="plain">
        {{ t('server.files.preview.truncatedHint') }}
      </ElTag>
    </div>

    <div
      v-loading="loading"
      :element-loading-text="t('server.files.preview.loading')"
      class="preview-body"
      :class="{ 'preview-body--ide': ideMode }"
    >
      <template v-if="!loading">
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
          <div v-if="ocrLoading || ocrText || ocrError" class="ocr-result">
            <div class="ocr-result__head">
              <span class="yg-text-xs yg-text-tertiary">{{ t('server.files.preview.ocrResult') }}</span>
              <ElButton
                v-if="ocrText"
                text
                bg
                size="small"
                :icon="CopyDocument"
                @click="copyOcrText"
              >
                {{ t('server.files.preview.copy') }}
              </ElButton>
            </div>
            <p v-if="ocrError" class="ocr-result__error">{{ ocrError }}</p>
            <pre v-else class="ocr-result__text">{{ ocrLoading ? t('server.files.preview.ocrRunning') : ocrText }}</pre>
          </div>
        </div>

        <!-- 文本(含 .md 源码态)统一进 Z-IDE:Monaco 预览 / 编辑 / 渲染预览切换都在里面 -->
        <ZIde
          v-else-if="isText"
          ref="ideRef"
          :path="path"
          :name="name"
          :content="text"
          :encoding="fileEncoding"
          :editable="canEdit"
          :readonly-reason="readonlyReason"
          :markdown="isMdFile"
          @saved="onSaved"
          @analyze="openAnalysis"
          @close="visible = false"
        />
      </template>
    </div>

    <template #footer>
      <ElButton
        v-if="canOcr"
        :icon="Document"
        :loading="ocrLoading"
        :disabled="ocrLoading"
        @click="runOcr"
      >
        {{ t('server.files.preview.ocr') }}
      </ElButton>
      <ElButton v-if="isText" :icon="CopyDocument" @click="copyText">
        {{ t('server.files.preview.copy') }}
      </ElButton>
      <ElButton type="primary" :icon="Download" @click="download">
        {{ t('server.files.download') }}
      </ElButton>
    </template>
  </ElDialog>

  <ZSceneRunDialog
    v-model="analysisVisible"
    scene="file_edit"
    :title="t('server.files.preview.aiAnalyze')"
    :context="analysisContext"
    @apply="onApplyResult"
  />

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

/* ---------- 全屏 Z-IDE:把 EP 弹窗拆成「header(隐藏) + body(撑满) + footer」 ----------
   弹窗根节点由 ElDialog 渲染(teleport 到 body),scoped 的 :deep 够不到根节点,
   所以用 :global 挂在自己独有的 class 上,不外溢。 */
:global(.el-dialog.file-preview-dialog--ide) {
  --el-dialog-padding-primary: 0;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

:global(.el-dialog.file-preview-dialog--ide .el-dialog__header) {
  display: none;
}

:global(.el-dialog.file-preview-dialog--ide .el-dialog__body) {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  padding: 0;
}

:global(.el-dialog.file-preview-dialog--ide .el-dialog__footer) {
  padding: var(--yg-space-2) var(--yg-space-4);
  border-top: 1px solid var(--yg-border-subtle);
  background: var(--yg-bg-surface);
}

.preview-body {
  display: flex;
  flex-direction: column;
  min-height: 340px;
  max-height: 66vh;
}

/* IDE 态:editor + 状态栏一起吃掉 body 的全部高度 */
.preview-body--ide {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  padding: var(--yg-space-2);
}

.preview-state {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
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

/* 识别结果：贴图片下方,与预览区同样的发丝边框,长文本内部滚动 */
.ocr-result {
  align-self: stretch;
  width: 100%;
  max-height: 220px;
  margin-top: var(--yg-space-2);
  padding: var(--yg-space-2) var(--yg-space-3);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  overflow: auto;
}

.ocr-result__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
  padding-bottom: var(--yg-space-1);
}

.ocr-result__text {
  margin: 0;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.ocr-result__error {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-danger);
}

.zide-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 40vh;
  padding: var(--yg-space-6);
}

.zide-loading__spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--yg-border-subtle);
  border-top-color: var(--yg-text-tertiary);
  border-radius: 999px;
  animation: zide-loading-spin 0.8s linear infinite;
}

@keyframes zide-loading-spin {
  to {
    transform: rotate(360deg);
  }
}</style>

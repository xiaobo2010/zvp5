<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download, Refresh, Close } from '@element-plus/icons-vue'
import { previewFile, downloadFile, type FilePreviewData } from '@/api/system/files.ts'
import ZMarkdown from '@/components/markdown/ZMarkdown.vue'

const SMALL_TEXT_LIMIT = 200 * 1024
const MAX_ROWS = 5000

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
const encodingLabel = computed(() => preview.value?.encoding || '—')

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

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') lightbox.value = false
}

watch(lightbox, (open) => {
  if (open) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

watch(
  () => props.modelValue,
  (open) => {
    if (open) void fetchPreview()
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
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('server.files.preview.title') + ' · ' + name"
    width="min(960px, 94vw)"
    :close-on-click-modal="false"
  >
    <div class="meta-row">
      <ElTag size="small" effect="plain" class="font-mono">{{ mimeLabel }}</ElTag>
      <span class="z-label tabular-nums">{{ sizeLabel }}</span>
      <span v-if="isText && !skipped" class="z-label font-mono">
        {{ t('server.files.preview.encodingLabel') }}: {{ encodingLabel }}
      </span>
      <ElTag v-if="preview?.truncated" size="small" type="warning" effect="plain">
        {{ t('server.files.preview.truncatedHint') }}
      </ElTag>
      <span class="min-w-0 flex-1" />
      <template v-if="isText">
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

    <div v-loading="loading" :element-loading-text="t('server.files.preview.loading')" class="preview-body">
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
          <div class="z-label pt-2 text-center tabular-nums">
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
    </div>

    <template #footer>
      <ElButton v-if="isText" :icon="CopyDocument" @click="copyText">
        {{ t('server.files.preview.copy') }}
      </ElButton>
      <ElButton type="primary" :icon="Download" @click="download">{{ t('server.files.download') }}</ElButton>
    </template>
  </ElDialog>

  <Teleport to="body">
    <div
      v-if="lightbox"
      class="fixed inset-0 z-[60] flex items-center justify-center bg-black/85 p-6"
      @click="lightbox = false"
    >
      <button type="button" class="absolute right-4 top-4 text-2xl text-white hover:opacity-80" @click="lightbox = false">
        <ElIcon><Close /></ElIcon>
      </button>
      <img :src="imageSrc" :alt="name" class="max-h-full max-w-full rounded shadow-2xl" @click.stop />
    </div>
  </Teleport>
</template>

<style scoped>
.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding-bottom: 10px;
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
  color: var(--zv-label);
  user-select: none;
}

.row-text {
  min-width: 0;
}

.rows-tail {
  padding: 6px 0 2px 52px;
  color: var(--zv-label);
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

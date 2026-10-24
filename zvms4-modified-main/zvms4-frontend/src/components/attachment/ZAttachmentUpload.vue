<script lang="ts" setup>
import {
  ref,
  computed,
  watch,
  onUnmounted,
  nextTick
} from 'vue'
import {
  ElButton,
  ElDialog,
  ElProgress,
  ElIcon,
  ElImageViewer,
  ElMessage
} from 'element-plus'
import {
  Camera,
  Picture,
  Delete,
  ZoomIn,
  Loading,
  Plus,
  Document
} from '@element-plus/icons-vue'
import type { AxiosProgressEvent } from 'axios'
import { useI18n } from 'vue-i18n'
import type {
  AttachmentMeta,
  AttachmentStatus,
  AttachmentUploadResult
} from '@/../types'
import * as attachmentApi from '@/api/attachment'
import { useUserStore } from '@/stores/user'
import { enqueueOcr } from '@/api/ai/ocr'
import { aiErrorCodeOf, aiErrorCopy } from '@/api/ai/errors'
import { useAiOcrPolling } from '@/components/ai/useAiOcrPolling'

interface DisplayItem {
  // 服务端 oid；上传中时可能为空串
  oid: string
  // 预览用 blob URL
  url: string
  // 原图预览 blob URL（按需加载）
  fullUrl?: string
  // 附件加载/上传状态
  status: 'loading' | 'uploading' | 'done' | 'error' | 'failed'
  // 上传进度 0-100
  progress: number
  // 元数据
  meta?: AttachmentMeta
  // 是否来自本地 File 临时预览
  isLocal?: boolean
  // 原始文件引用，失败后可重试
  file?: File
  // OCR 实时状态（网关受理后由轮询维护）
  ocr?: {
    status: 'pending' | 'processing' | 'done' | 'failed'
    engine?: string
    confidence?: number
    text?: string
    error?: string
  }
}

const props = withDefaults(
  defineProps<{
    // 已上传附件 oid 列表
    modelValue?: string[]
    // 附件用途，当前仅支持 volunteer_order
    purpose?: 'volunteer_order'
    // 最多允许的图片数量
    max?: number
    // 禁用全部交互
    disabled?: boolean
    // 仅展示，不显示上传/删除按钮
    readonly?: boolean
    // 上传时绑定到的实体类型（如 activity）
    entityType?: string
    // 上传时绑定到的实体 oid；两者需同时提供才会随上传提交
    entityOid?: string
  }>(),
  {
    modelValue: () => [],
    purpose: 'volunteer_order',
    max: 5,
    disabled: false,
    readonly: false
  }
)

const emit = defineEmits<{
  'update:modelValue': [value: string[]]
  uploaded: [results: AttachmentUploadResult[]]
  deleted: [oid: string]
  error: [err: unknown]
}>()

const items = ref<DisplayItem[]>([])
const cameraInput = ref<HTMLInputElement>()
const galleryInput = ref<HTMLInputElement>()
const previewUrl = ref<string>()
const previewVisible = ref(false)
const user = useUserStore()
const { t } = useI18n()

// OCR 走网关（/ai/ocr）：有额度与频控，普通学生看不到入口。
const canOcr = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)
const OCR_MIME = ['image/jpeg', 'image/png', 'image/webp']

const countText = computed(() => `${items.value.length}/${props.max}`)
const canAdd = computed(
  () => items.value.length < props.max && !props.disabled && !props.readonly
)

// —— OCR：受理 + 轮询 + 结果展示 ——

const ocrResultVisible = ref(false)
const ocrResultItem = ref<DisplayItem | null>(null)
const ocrBusy = ref('')

const polling = useAiOcrPolling({
  onUpdate: (oid, result) => {
    const item = items.value.find((entry) => entry.oid === oid)
    if (!item) {
      return
    }
    item.ocr = {
      status: result.status,
      engine: result.engine ?? item.ocr?.engine,
      confidence: result.confidence,
      text: result.text
    }
  },
  onError: (oid) => {
    const item = items.value.find((entry) => entry.oid === oid)
    if (item) {
      item.ocr = { status: 'failed', error: item.ocr?.error }
    }
  }
})

function itemOcrStatus(item: DisplayItem): string | undefined {
  if (item.ocr) {
    return item.ocr.status
  }
  return item.meta?.ocr_status
}

function supportsOcr(item: DisplayItem): boolean {
  if (!canOcr.value || !item.oid || item.status !== 'done') {
    return false
  }
  const mime = (item.meta?.mime ?? '').toLowerCase()
  return OCR_MIME.includes(mime)
}

function ocrRunning(item: DisplayItem): boolean {
  const status = itemOcrStatus(item)
  return status === 'pending' || status === 'processing'
}

function ocrButtonTitle(item: DisplayItem): string {
  if (ocrRunning(item)) {
    return t('ai.ocr.running')
  }
  if (itemOcrStatus(item) === 'done') {
    return t('ai.ocr.viewText')
  }
  if (itemOcrStatus(item) === 'failed') {
    return t('ai.ocr.retry')
  }
  return t('ai.ocr.action')
}

function onOcrClick(item: DisplayItem) {
  if (ocrRunning(item)) {
    return
  }
  const status = itemOcrStatus(item)
  if (status === 'done') {
    openOcrResult(item)
    return
  }
  void startOcr(item)
}

async function startOcr(item: DisplayItem) {
  if (!item.oid || ocrBusy.value) {
    return
  }
  ocrBusy.value = item.oid
  try {
    const result = await enqueueOcr(item.oid)
    item.ocr = {
      status: result.status === 'pending' ? 'pending' : 'processing',
      engine: result.engine
    }
    polling.start(item.oid)
  } catch (error) {
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    item.ocr = { status: 'failed', error: `${copy.title}：${copy.hint}` }
    ElMessage({
      message: `${copy.title}：${copy.hint}`,
      type: 'error',
      plain: true,
      grouping: true
    })
  } finally {
    ocrBusy.value = ''
  }
}

function openOcrResult(item: DisplayItem) {
  ocrResultItem.value = item
  ocrResultVisible.value = true
}

async function copyOcrText() {
  const text = ocrResultItem.value?.ocr?.text ?? ''
  if (!text) {
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage({ message: t('ai.common.copied'), type: 'success', plain: true, grouping: true })
  } catch {
    ElMessage({ message: text, type: 'warning', plain: true, grouping: true })
  }
}

watch(
  () => props.modelValue,
  async (oids) => {
    if (!oids) return
    const wanted = new Set(oids)
    const toRemove = items.value.filter((i) => !wanted.has(i.oid))
    for (const item of toRemove) {
      polling.stop(item.oid)
      revokeItem(item)
    }
    items.value = items.value.filter((i) => wanted.has(i.oid))
    const current = new Set(items.value.map((i) => i.oid))
    for (const oid of oids) {
      if (!current.has(oid)) {
        await loadStored(oid)
      }
    }
  },
  { immediate: true, deep: true }
)

onUnmounted(() => {
  // 组件销毁时停掉 OCR 轮询，不让定时器在卸载后继续发请求。
  polling.stopAll()
  items.value.forEach(revokeItem)
})

// 视为可用的存量附件状态；其余（processing / rejected / quarantined / deleted 等）一律静默移除
const STORED_META_OK: ReadonlySet<AttachmentStatus> = new Set([
  'stored',
  'ready',
  'verified'
])

async function loadStored(oid: string) {
  const index =
    items.value.push({
      oid,
      url: '',
      status: 'loading',
      progress: 0
    }) - 1
  try {
    const meta = await attachmentApi.read.getMeta(oid)
    // 后端已判定该附件不可用（如软删行 status=deleted）：静默移除
    if (meta?.status && !STORED_META_OK.has(meta.status)) {
      dropStoredItem(oid)
      return
    }
    const blob = await attachmentApi.read.getThumb(oid)
    const url = URL.createObjectURL(blob)
    // 等待期间 modelValue 变化会触发 watch 重排数组，槽位可能已不是本项
    const target = items.value[index]
    if (!target || target.oid !== oid) {
      URL.revokeObjectURL(url)
      return
    }
    target.url = url
    target.meta = meta
    target.status = 'done'
    // 上一次识别还没跑完（对话框关过、或别人发起的）：接上轮询，
    // 不让状态永远停在「识别中」。
    const metaOcrStatus = meta?.ocr_status
    if (canOcr.value && (metaOcrStatus === 'pending' || metaOcrStatus === 'processing')) {
      target.ocr = { status: metaOcrStatus }
      polling.start(oid)
    }
  } catch (err) {
    const http = (err as { response?: { status?: number } }).response?.status
    // 404/410：存量附件已不存在或已软删，静默移除，不污染父级
    if (http === 404 || http === 410) {
      dropStoredItem(oid)
      return
    }
    // 其它失败（断网/5xx/超时）：保留占位，显示「加载失败」，只提供删除按钮
    const target = items.value.find((i) => i.oid === oid)
    if (target) {
      target.status = 'failed'
      target.url = ''
    }
  }
}

// 静默移除一个不可用的存量附件：释放预览、移出 items 并同步 v-model
function dropStoredItem(oid: string) {
  const target = items.value.find((i) => i.oid === oid)
  if (!target) return
  polling.stop(oid)
  revokeItem(target)
  items.value.splice(items.value.indexOf(target), 1)
  syncModel()
}

function revokeItem(item: DisplayItem) {
  if (item.url) {
    URL.revokeObjectURL(item.url)
  }
  if (item.fullUrl) {
    URL.revokeObjectURL(item.fullUrl)
  }
}

function syncModel() {
  emit(
    'update:modelValue',
    items.value.filter((i) => i.oid).map((i) => i.oid)
  )
}

function triggerCamera() {
  cameraInput.value?.click()
}

function triggerGallery() {
  galleryInput.value?.click()
}

async function onInputChange(evt: Event, source: 'camera' | 'gallery') {
  const target = evt.target as HTMLInputElement
  const selected = Array.from(target.files ?? [])
  target.value = ''
  if (selected.length === 0) {
    ElMessage({
      message: '未选择文件',
      type: 'warning',
      plain: true,
      grouping: true
    })
    return
  }
  await uploadFiles(selected)
}

async function uploadFiles(fileList: FileList | File[]) {
  const available = Math.max(0, props.max - items.value.length)
  if (available === 0) {
    ElMessage({
      message: `最多上传 ${props.max} 张照片`,
      type: 'warning',
      plain: true,
      grouping: true
    })
    return
  }
  const toUpload = Array.from(fileList).slice(0, available)
  for (const file of toUpload) {
    await uploadSingle(file)
  }
}

async function uploadSingle(file: File, replaceIndex?: number) {
  const tempUrl = URL.createObjectURL(file)
  const item: DisplayItem = {
    oid: '',
    url: tempUrl,
    status: 'uploading',
    progress: 0,
    isLocal: true,
    file
  }

  let index: number
  if (replaceIndex !== undefined && replaceIndex >= 0) {
    revokeItem(items.value[replaceIndex])
    items.value[replaceIndex] = item
    index = replaceIndex
  } else {
    index = items.value.push(item) - 1
  }

  const pushProgress = (progressEvent: AxiosProgressEvent) => {
    if (progressEvent.total) {
      items.value[index].progress = Math.round(
        (progressEvent.loaded * 100) / progressEvent.total
      )
    }
  }

  try {
    const results = await attachmentApi.upload(
      [file],
      props.purpose,
      props.entityType,
      props.entityOid,
      pushProgress
    )
    const result = results[0]
    if (!result) {
      throw new Error('服务端未返回上传结果')
    }
    items.value[index].oid = result._id
    items.value[index].meta = result
    items.value[index].isLocal = false
    items.value[index].status = 'done'
    syncModel()
    emit('uploaded', [result])
  } catch (err) {
    items.value[index].status = 'error'
    items.value[index].progress = 0
    // 不在此弹出 toast:axios 错误已由响应拦截器统一提示(401/后端 detail/未知错误),
    // 组件侧保留内联 error 状态与 emit,避免同一 detail 弹两次。
    emit('error', err)
  }
}

async function removeItem(item: DisplayItem) {
  if (props.disabled || props.readonly) return
  if (item.oid) {
    // 附件没了，它的识别轮询也没有意义，先停表。
    polling.stop(item.oid)
    try {
      await attachmentApi.manage.deleteOne(item.oid)
      emit('deleted', item.oid)
    } catch (err) {
      emit('error', err)
      return
    }
  }
  const index = items.value.indexOf(item)
  if (index !== -1) {
    revokeItem(item)
    items.value.splice(index, 1)
    syncModel()
  }
}

async function retryItem(item: DisplayItem) {
  if (!item.file) return
  const index = items.value.indexOf(item)
  await uploadSingle(item.file, index)
}

async function openPreview(item: DisplayItem) {
  if (!item.url) return
  if (item.fullUrl) {
    previewUrl.value = item.fullUrl
  } else if (item.oid) {
    try {
      const blob = await attachmentApi.read.getBytes(item.oid)
      item.fullUrl = URL.createObjectURL(blob)
      previewUrl.value = item.fullUrl
    } catch (err) {
      emit('error', err)
      ElMessage({
        message: '预览加载失败',
        type: 'error',
        plain: true,
        grouping: true
      })
      return
    }
  } else {
    previewUrl.value = item.url
  }
  previewVisible.value = true
}

function closePreview() {
  previewVisible.value = false
  nextTick(() => {
    previewUrl.value = undefined
  })
}

function statusText(item: DisplayItem): string {
  if (item.status === 'loading') return '加载中…'
  if (item.status === 'uploading') return '上传中…'
  if (item.status === 'error') return '上传失败'
  if (item.status === 'failed') return '加载失败'
  const live = item.ocr?.status
  if (live === 'processing' || live === 'pending') return t('ai.ocr.running')
  if (live === 'done') return t('ai.ocr.done')
  if (live === 'failed') return t('ai.ocr.failed')
  const ocr = item.meta?.ocr_status
  if (ocr === 'processing') return '识别中'
  if (ocr === 'done') return '已识别'
  if (ocr === 'failed') return '识别失败'
  return '待识别'
}
</script>

<template>
  <div class="yg-upload yg-upload--picture yg-attachment-upload">
    <div class="flex items-center justify-between">
      <span class="yg-text-sm yg-text-secondary">义工单照片</span>
      <span class="yg-text-xs yg-text-tertiary">{{ countText }}</span>
    </div>

    <!-- 空态：拖拽上传区（准则 4.2.10） -->
    <div v-if="items.length === 0" class="yg-upload__drag">
      <ElIcon class="yg-text-2xl yg-text-tertiary">
        <Picture />
      </ElIcon>
      <p class="yg-text-sm yg-text-tertiary">尚未上传照片</p>
      <div v-if="canAdd" class="flex justify-center gap-3">
        <ElButton type="primary" plain @click="triggerCamera">
          <ElIcon class="mr-1"><Camera /></ElIcon>拍照
        </ElButton>
        <ElButton @click="triggerGallery">
          <ElIcon class="mr-1"><Picture /></ElIcon>相册
        </ElButton>
      </div>
    </div>

    <!-- 图片墙 -->
    <div v-else class="yg-upload__list yg-attachment-upload__grid">
      <div
        v-for="item in items"
        :key="item.oid || item.url"
        class="yg-attachment-upload__item"
      >
        <div class="yg-upload__thumb yg-attachment-upload__thumb">
          <div
            v-if="item.status === 'loading'"
            class="yg-attachment-upload__media"
          >
            <ElIcon class="yg-attachment-upload__spin yg-text-xl yg-text-tertiary">
              <Loading />
            </ElIcon>
          </div>
          <img
            v-else-if="item.url"
            :src="item.url"
            alt="附件预览"
          />
          <div
            v-else
            class="yg-attachment-upload__media yg-text-xs yg-text-tertiary"
          >
            无预览
          </div>
        </div>

        <!-- 上传中：遮罩 + 进度 -->
        <div v-if="item.status === 'uploading'" class="yg-attachment-upload__overlay">
          <ElProgress
            :percentage="item.progress"
            :show-text="false"
            :stroke-width="4"
            class="w-full"
          />
          <span class="yg-text-xs yg-text-inverse mt-2">上传中…</span>
        </div>

        <!-- 上传失败 -->
        <div
          v-else-if="item.status === 'error'"
          class="yg-attachment-upload__overlay"
        >
          <span class="yg-text-xs yg-text-inverse mb-2">上传失败</span>
          <div class="flex gap-1">
            <button
              type="button"
              class="yg-attachment-upload__ghost-btn"
              @click="retryItem(item)"
            >
              重试
            </button>
            <button
              type="button"
              class="yg-attachment-upload__ghost-btn"
              @click="removeItem(item)"
            >
              删除
            </button>
          </div>
        </div>

        <!-- 加载失败 -->
        <div
          v-else-if="item.status === 'failed'"
          class="yg-attachment-upload__overlay"
        >
          <span class="yg-text-xs yg-text-inverse mb-2">加载失败</span>
          <button
            type="button"
            class="yg-attachment-upload__ghost-btn"
            @click="removeItem(item)"
          >
            删除
          </button>
        </div>

        <!-- 已完成：状态条 + 图标操作 -->
        <div
          v-else-if="item.status === 'done'"
          class="yg-attachment-upload__bar"
        >
          <span class="yg-text-xs yg-text-inverse truncate">{{ statusText(item) }}</span>
          <div class="flex items-center">
            <button
              v-if="supportsOcr(item) || item.ocr"
              type="button"
              class="yg-attachment-upload__icon-btn"
              :title="ocrButtonTitle(item)"
              @click="onOcrClick(item)"
            >
              <ElIcon :class="ocrRunning(item) ? 'yg-attachment-upload__spin' : ''">
                <Loading v-if="ocrRunning(item)" />
                <Document v-else />
              </ElIcon>
            </button>
            <button
              v-if="item.url"
              type="button"
              class="yg-attachment-upload__icon-btn"
              @click="openPreview(item)"
            >
              <ElIcon><ZoomIn /></ElIcon>
            </button>
            <!-- 删除属于破坏性操作：用红图标（准则 5.1） -->
            <button
              v-if="!readonly && !disabled"
              type="button"
              class="yg-attachment-upload__icon-btn yg-attachment-upload__icon-btn--danger"
              @click="removeItem(item)"
            >
              <ElIcon><Delete /></ElIcon>
            </button>
          </div>
        </div>
      </div>

      <button
        v-if="canAdd"
        type="button"
        class="yg-attachment-upload__item yg-attachment-upload__item--add"
        @click="triggerCamera"
      >
        <ElIcon class="yg-text-xl"><Plus /></ElIcon>
        <span class="yg-text-xs">继续拍照</span>
      </button>
    </div>

    <div v-if="!readonly && !disabled && items.length > 0" class="mt-3 flex gap-3">
      <ElButton type="primary" plain @click="triggerCamera">
        <ElIcon class="mr-1"><Camera /></ElIcon>拍照
      </ElButton>
      <ElButton @click="triggerGallery">
        <ElIcon class="mr-1"><Picture /></ElIcon>从相册选择
      </ElButton>
    </div>

    <input
      ref="cameraInput"
      type="file"
      accept="image/*"
      capture="environment"
      class="hidden"
      @change="onInputChange($event, 'camera')"
    />
    <input
      ref="galleryInput"
      type="file"
      accept="image/*"
      multiple
      class="hidden"
      @change="onInputChange($event, 'gallery')"
    />

    <ElImageViewer
      v-if="previewVisible && previewUrl"
      :url-list="[previewUrl]"
      teleported
      hide-on-click-modal
      @close="closePreview"
    />

    <ElDialog
      v-model="ocrResultVisible"
      :title="t('ai.ocr.dialogTitle')"
      width="min(720px, 94vw)"
      append-to-body
    >
      <div class="flex flex-col gap-2">
        <div class="flex flex-wrap items-center gap-3 yg-text-xs yg-text-tertiary">
          <span v-if="ocrResultItem?.ocr?.engine">
            {{ t('ai.ocr.engine') }}：{{ ocrResultItem.ocr.engine }}
          </span>
          <span v-if="ocrResultItem?.ocr?.confidence !== undefined" class="tabular-nums">
            {{ t('ai.ocr.confidence') }}：{{ Math.round((ocrResultItem.ocr.confidence ?? 0) * 100) }}%
          </span>
          <span v-if="ocrResultItem?.ocr?.error" class="yg-text-danger">
            {{ ocrResultItem.ocr.error }}
          </span>
        </div>
        <pre class="yg-attachment-upload__ocr-text">{{
          ocrResultItem?.ocr?.text || t('ai.ocr.emptyText')
        }}</pre>
      </div>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton @click="ocrResultVisible = false">{{ t('ai.common.close') }}</ElButton>
          <ElButton
            type="primary"
            :disabled="!ocrResultItem?.ocr?.text"
            @click="copyOcrText"
          >
            {{ t('ai.ocr.copyText') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
/* 图片墙：8px 网格上的 96px 缩略图 */
.yg-attachment-upload__grid {
  display: grid;
  gap: var(--yg-space-3);
  grid-template-columns: repeat(auto-fill, minmax(calc(var(--yg-space-9) * 2), 1fr));
}

.yg-attachment-upload__item {
  position: relative;
  aspect-ratio: 1 / 1;
  overflow: hidden;
  border-radius: var(--yg-radius-lg);
  background-color: var(--yg-bg-surface-sunken);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-default);
}

.yg-attachment-upload__thumb {
  width: 100%;
  height: 100%;
  border-radius: var(--yg-radius-lg);
}

/* 占位（加载中 / 无预览） */
.yg-attachment-upload__media {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

/* 遮罩层：深色不透明底（不支持 color-mix 时退化为实色，文字始终可读） */
.yg-attachment-upload__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-inline: var(--yg-space-2);
  background-color: var(--yg-gray-950);
  background-color: color-mix(in srgb, var(--yg-gray-950) 72%, transparent);
  text-align: center;
}

/* 底部状态条 */
.yg-attachment-upload__bar {
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-1);
  padding: var(--yg-space-1) var(--yg-space-2);
  background-color: var(--yg-gray-950);
  background-color: color-mix(in srgb, var(--yg-gray-950) 62%, transparent);
}

.yg-attachment-upload__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--yg-space-6);
  height: var(--yg-space-6);
  border-radius: var(--yg-radius-sm);
  color: var(--yg-text-inverse);
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-attachment-upload__icon-btn:hover {
  background-color: var(--yg-bg-hover);
}

.yg-attachment-upload__icon-btn:focus-visible {
  box-shadow: var(--yg-focus-ring);
}

/* 删除：破坏性操作，用红色（准则 5.1） */
.yg-attachment-upload__icon-btn--danger {
  color: var(--yg-text-danger);
}

.yg-attachment-upload__ghost-btn {
  padding: var(--yg-space-1) var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  color: var(--yg-text-inverse);
  font-size: var(--yg-font-size-xs);
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-attachment-upload__ghost-btn:hover {
  background-color: var(--yg-bg-hover);
}

/* 继续拍照：虚线占位块 */
.yg-attachment-upload__item--add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--yg-space-1);
  border-style: dashed;
  color: var(--yg-text-tertiary);
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-attachment-upload__item--add:hover {
  color: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
}

/* OCR 文本：凹陷面 + 发丝线（实色，可读性优先） */
.yg-attachment-upload__ocr-text {
  margin: 0;
  padding: var(--yg-space-3);
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: var(--yg-line-height-relaxed);
  font-size: var(--yg-font-size-md);
  font-family: var(--yg-font-mono);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background-color: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
}

.yg-attachment-upload__spin {
  animation: yg-attachment-upload__spin 1s linear infinite;
}

@keyframes yg-attachment-upload__spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .yg-attachment-upload__spin {
    animation: none;
  }
}

@supports (corner-shape: squircle) {
  .yg-attachment-upload__item,
  .yg-attachment-upload__thumb,
  .yg-attachment-upload__ocr-text {
    corner-shape: squircle;
  }
}
</style>

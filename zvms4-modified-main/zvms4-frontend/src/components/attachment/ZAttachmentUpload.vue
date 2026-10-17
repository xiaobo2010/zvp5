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
  ElProgress,
  ElIcon,
  ElMessage
} from 'element-plus'
import {
  Camera,
  Picture,
  Delete,
  ZoomIn,
  Plus,
  Close
} from '@element-plus/icons-vue'
import type { AxiosProgressEvent } from 'axios'
import type {
  AttachmentMeta,
  AttachmentUploadResult
} from '@/../types'
import * as attachmentApi from '@/api/attachment'

interface DisplayItem {
  // 服务端 oid；上传中时可能为空串
  oid: string
  // 预览用 blob URL
  url: string
  // 原图预览 blob URL（按需加载）
  fullUrl?: string
  // 上传状态
  status: 'uploading' | 'done' | 'error'
  // 上传进度 0-100
  progress: number
  // 元数据
  meta?: AttachmentMeta
  // 是否来自本地 File 临时预览
  isLocal?: boolean
  // 原始文件引用，失败后可重试
  file?: File
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

const countText = computed(() => `${items.value.length}/${props.max}`)
const canAdd = computed(
  () => items.value.length < props.max && !props.disabled && !props.readonly
)

watch(
  () => props.modelValue,
  async (oids) => {
    if (!oids) return
    const wanted = new Set(oids)
    const toRemove = items.value.filter((i) => !wanted.has(i.oid))
    for (const item of toRemove) {
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
  items.value.forEach(revokeItem)
})

async function loadStored(oid: string) {
  const index =
    items.value.push({
      oid,
      url: '',
      status: 'uploading',
      progress: 0
    }) - 1
  try {
    const meta = await attachmentApi.read.getMeta(oid)
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
  } catch (err) {
    const target = items.value[index]
    if (target && target.oid === oid) {
      target.status = 'error'
      target.url = ''
    }
    emit('error', err)
  }
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
    items.value.filter((i) => i.oid && i.status !== 'error').map((i) => i.oid)
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
  if (item.oid && item.status === 'done') {
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
  if (item.status === 'uploading') return '上传中…'
  if (item.status === 'error') return '上传失败'
  const ocr = item.meta?.ocr_status
  if (ocr === 'processing') return '识别中'
  if (ocr === 'done') return '已识别'
  if (ocr === 'failed') return '识别失败'
  return '待识别'
}
</script>

<template>
  <div class="w-full">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-gray-700">义工单照片</span>
      <span class="text-xs text-gray-500">{{ countText }}</span>
    </div>

    <div
      v-if="items.length === 0"
      class="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-6 text-center"
    >
      <ElIcon class="text-2xl text-gray-400 mb-2">
        <Picture />
      </ElIcon>
      <p class="text-sm text-gray-500 mb-4">尚未上传照片</p>
      <div v-if="canAdd" class="flex justify-center gap-3">
        <ElButton type="primary" plain @click="triggerCamera">
          <ElIcon class="mr-1"><Camera /></ElIcon>拍照
        </ElButton>
        <ElButton @click="triggerGallery">
          <ElIcon class="mr-1"><Picture /></ElIcon>相册
        </ElButton>
      </div>
    </div>

    <div
      v-else
      class="grid gap-3"
      :style="{
        gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))'
      }"
    >
      <div
        v-for="item in items"
        :key="item.oid || item.url"
        class="relative aspect-square rounded-lg overflow-hidden border border-gray-200 bg-gray-100"
      >
        <img
          v-if="item.url"
          :src="item.url"
          class="w-full h-full object-cover"
          alt="附件预览"
        />
        <div
          v-else
          class="w-full h-full flex items-center justify-center text-xs text-gray-500"
        >
          无预览
        </div>

        <div
          v-if="item.status === 'uploading'"
          class="absolute inset-0 bg-black/50 flex flex-col items-center justify-center px-2"
        >
          <ElProgress
            :percentage="item.progress"
            :show-text="false"
            :stroke-width="4"
            class="w-full"
          />
          <span class="text-white text-xs mt-2">上传中…</span>
        </div>

        <div
          v-else-if="item.status === 'error'"
          class="absolute inset-0 bg-black/60 flex flex-col items-center justify-center px-2 text-center"
        >
          <span class="text-white text-xs mb-2">上传失败</span>
          <div class="flex gap-1">
            <button
              type="button"
              class="text-white text-xs px-2 py-1 rounded hover:bg-white/20"
              @click="retryItem(item)"
            >
              重试
            </button>
            <button
              type="button"
              class="text-white text-xs px-2 py-1 rounded hover:bg-white/20"
              @click="removeItem(item)"
            >
              删除
            </button>
          </div>
        </div>

        <div
          v-else
          class="absolute inset-x-0 bottom-0 bg-black/40 px-1 py-0.5 flex items-center justify-between"
        >
          <span class="text-white text-[10px] truncate">{{ statusText(item) }}</span>
          <div class="flex items-center">
            <button
              v-if="item.url"
              type="button"
              class="text-white p-1 rounded hover:bg-white/20"
              @click="openPreview(item)"
            >
              <ElIcon><ZoomIn /></ElIcon>
            </button>
            <button
              v-if="!readonly && !disabled"
              type="button"
              class="text-white p-1 rounded hover:bg-white/20"
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
        class="aspect-square rounded-lg border border-dashed border-gray-300 flex flex-col items-center justify-center text-gray-500 hover:border-primary hover:text-primary transition-colors"
        @click="triggerCamera"
      >
        <ElIcon class="text-xl mb-1"><Plus /></ElIcon>
        <span class="text-xs">继续拍照</span>
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

    <Teleport to="body">
      <div
        v-if="previewVisible"
        class="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
        @click="closePreview"
      >
        <button
          type="button"
          class="absolute top-4 right-4 text-white text-2xl"
          @click="closePreview"
        >
          <ElIcon><Close /></ElIcon>
        </button>
        <img
          v-if="previewUrl"
          :src="previewUrl"
          class="max-w-full max-h-full rounded shadow-lg"
          alt="大图预览"
          @click.stop
        />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.text-gray-700 {
  color: var(--el-text-color-regular);
}
.text-gray-500 {
  color: var(--el-text-color-secondary);
}
.text-gray-400 {
  color: var(--el-text-color-placeholder);
}
.border-gray-300 {
  border-color: var(--el-border-color);
}
.border-gray-200 {
  border-color: var(--el-border-color-light);
}
.bg-gray-50 {
  background-color: var(--el-fill-color-light);
}
.bg-gray-100 {
  background-color: var(--el-fill-color);
}
.hover\:border-primary:hover {
  border-color: var(--el-color-primary);
}
.hover\:text-primary:hover {
  color: var(--el-color-primary);
}
</style>

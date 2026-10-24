<script lang="ts" setup>
import { ElButton, ElResult, ElSwitch } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { Refresh, Tickets } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { ZButtonOrCard } from '@/components'
import ZAttachmentUpload from '@/components/attachment/ZAttachmentUpload.vue'
import * as attachmentApi from '@/api/attachment'
import type { AttachmentListItem } from '@/../types'
import { useUserStore } from '@/stores/user'
import { getXuehaiId } from '@/plugins/ua'

// 管理单个活动的义工单附件：列出、上传、删除。
// 权限与 ZActivityStatsDialog 的入口不同：仅 manager（admin/department）
// 且非学海设备可见（后端对附件 API 携带 Xuehai-Id 一律 403）。
const props = withDefaults(
  defineProps<{
    activityId?: string
    aid?: string
    max?: number
  }>(),
  {
    activityId: '',
    aid: '',
    max: 5
  }
)

const { t } = useI18n()
const user = useUserStore()

const activityOid = computed(() => props.activityId || props.aid)
const canManage = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)
// 与 ZAttachmentUpload 内的判定保持一致：OCR 有额度与频控，只对管理员开放。
const canOcr = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)
const isXuehaiDevice = computed(() => getXuehaiId() > 0)
const gateBlocked = computed(() => !canManage.value || isXuehaiDevice.value)
const gateText = computed(() => {
  if (isXuehaiDevice.value) {
    return t('activity.form.volunteerOrder.deviceUnsupported')
  }
  return t('activity.form.volunteerOrder.noPermission')
})

const open = ref(false)
const loading = ref(false)
const error = ref('')
const oids = ref<string[]>([])
// 默认关闭：自动识别会消耗 OCR 额度，必须由用户自己勾选。
const autoOcr = ref(false)

function oidOf(item: AttachmentListItem): string {
  const raw = item as unknown as { id?: string; _id?: string }
  return raw.id || raw._id || ''
}

async function load(force = false) {
  if (!activityOid.value || gateBlocked.value) return
  if (loading.value && !force) return
  loading.value = true
  error.value = ''
  try {
    const result = await attachmentApi.read.list('all', {
      entityType: 'activity',
      entityOid: activityOid.value,
      purpose: 'volunteer_order',
      page: 1,
      size: 50
    })
    oids.value = result.data.map(oidOf).filter((oid) => oid.length > 0)
  } catch {
    error.value = t('activity.form.volunteerOrder.loadFailed')
  } finally {
    loading.value = false
  }
}

function reload() {
  void load(true)
}

watch(open, () => {
  if (open.value) {
    void load()
  }
})
</script>

<template>
  <ZButtonOrCard
    class="px-2"
    v-model:open="open"
    mode="button"
    pop-type="dialog"
    fullscreen
    type="primary"
    :icon="Tickets"
  >
    <template #default>
      <div v-loading="loading" class="yg-attachment-dialog">
        <div class="yg-attachment-dialog__toolbar">
          <ElButton
            class="px-2"
            type="success"
            :icon="Refresh"
            :disabled="loading"
            text
            bg
            circle
            :title="t('activity.form.actions.refresh')"
            :aria-label="t('activity.form.actions.refresh')"
            @click="reload"
          />
        </div>
        <ElResult v-if="gateBlocked" icon="warning" :title="gateText" />
        <ElResult v-else-if="error && !loading" icon="error" :title="error" />
        <template v-else>
          <!-- 显式选择：默认关闭，开启后每张上传成功的图片自动走 OCR -->
          <div v-if="canOcr" class="yg-attachment-dialog__option">
            <ElSwitch
              v-model="autoOcr"
              :aria-label="t('ai.ocr.autoLabel')"
              :disabled="loading"
            />
            <div class="yg-attachment-dialog__option-text">
              <span class="yg-text-sm">{{ t('ai.ocr.autoLabel') }}</span>
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.ocr.autoHint') }}</span>
            </div>
          </div>
          <ZAttachmentUpload
            v-model="oids"
            entity-type="activity"
            :entity-oid="activityOid"
            :max="max"
            :auto-ocr="autoOcr"
          />
        </template>
      </div>
    </template>
    <template #text>{{ t('activity.form.volunteerOrder.name') }}</template>
  </ZButtonOrCard>
</template>

<style scoped>
/* 全屏弹窗：正文铺满并留出统一呼吸空间，垂屏/横屏一致 */
.yg-attachment-dialog {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  padding: var(--yg-space-2) var(--yg-space-4) var(--yg-space-6);
}

.yg-attachment-dialog__toolbar {
  display: flex;
  justify-content: center;
}

/* 选项行：凹陷实色底，与上传区同一张卡片内并列 */
.yg-attachment-dialog__option {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  padding: var(--yg-space-3) var(--yg-space-4);
  border-radius: var(--yg-radius-lg);
  background-color: var(--yg-bg-surface-sunken);
}

.yg-attachment-dialog__option-text {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

/* 全屏下拖拽区留足高度，避免空态缩成一条 */
.yg-attachment-dialog :deep(.yg-upload__drag) {
  min-height: calc(var(--yg-space-9) * 4);
}
</style>

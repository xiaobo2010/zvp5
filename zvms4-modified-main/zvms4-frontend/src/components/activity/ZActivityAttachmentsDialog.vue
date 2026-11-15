<script lang="ts" setup>
import { ElButton, ElResult, ElIcon } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { Refresh, Tickets } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { ZButtonOrCard } from '@/components'
import ZAttachmentUpload from '@/components/attachment/ZAttachmentUpload.vue'
import * as attachmentApi from '@/api/attachment'
import type { AttachmentListItem } from '@/../types'
import { useUserStore } from '@/stores/user'
import { getXuehaiId } from '@/plugins/ua'
import Skeleton from '@/ui/ux/Skeleton.vue'

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
const { width, height } = useWindowSize()

const activityOid = computed(() => props.activityId || props.aid)
const canManage = computed(
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
    :width="width < height ? '60%' : '30%'"
    type="primary"
    :icon="Tickets"
  >
    <template #default>
      <div>
        <Skeleton v-if="loading" :lines="4" height="14px" />
        <div v-else style="text-align: center">
          <button
            class="yg2-icon-btn"
            type="button"
            :disabled="loading"
            :title="t('activity.form.actions.refresh')"
            :aria-label="t('activity.form.actions.refresh')"
            @click="reload"
          >
            <ElIcon><Refresh /></ElIcon>
          </button>
        </div>
        <ElResult v-if="gateBlocked" icon="warning" :title="gateText" />
        <ElResult v-else-if="error && !loading" icon="error" :title="error" />
        <ZAttachmentUpload
          v-else
          v-model="oids"
          entity-type="activity"
          :entity-oid="activityOid"
          :max="max"
        />
      </div>
    </template>
    <template #text>{{ t('activity.form.volunteerOrder.name') }}</template>
  </ZButtonOrCard>
</template>
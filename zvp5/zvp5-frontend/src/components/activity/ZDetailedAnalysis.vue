<script setup lang="ts">
import {
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElIcon,
  ElResult,
  ElRow,
  ElSegmented,
  ElTable,
  ElTableColumn
} from 'element-plus'
import { computed, ref, toRefs, watch } from 'vue'
import { InfoFilled, Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import dayjs from '@/plugins/dayjs'
import type { Activity, ActivityMember } from '@/../types/v2'
import type { UserActivityTimeSums } from '@/../types'
import { ZActivityDuration, ZActivityStatus } from '@/components'
import { displayStatus } from '@/utils/activityStatus'

type UserActivityMine = {
  _id: string
  member: string
  status: ActivityMember['status']
  mode: ActivityMember['mode']
  duration: number
}

type UserActivity = Activity & { mine?: UserActivityMine }

const props = withDefaults(
  defineProps<{
    id: string
    mode?: ActivityMember['mode']
    sums?: UserActivityTimeSums
  }>(),
  {
    id: '',
    mode: 'on-campus'
  }
)

const { id, sums } = toRefs(props)

const { t } = useI18n()

const modes: ActivityMember['mode'][] = ['on-campus', 'off-campus', 'social-practice']
const activeMode = ref<ActivityMember['mode']>(props.mode)

const records = ref<UserActivity[]>([])
const loading = ref(false)
const error = ref('')
const total = ref(0)

const page = ref(1)
const perpage = ref(10)

async function fetchRecords() {
  if (!id.value) {
    records.value = []
    total.value = 0
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await api.user.activity.read(id.value, page.value, perpage.value)
    records.value = result?.activities ?? []
    total.value = result?.total ?? records.value.length
  } catch {
    records.value = []
    total.value = 0
    error.value = '明细加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch([id, page], fetchRecords, { immediate: true })

watch(perpage, () => {
  if (page.value !== 1) {
    page.value = 1
  } else {
    fetchRecords()
  }
})

const filtered = computed(() =>
  records.value.filter((record) => record.mine?.mode === activeMode.value)
)

const hasRecords = computed(() => filtered.value.length > 0)

// Backend does not support server-side mode filtering, so the current page is
// filtered on the client while the total reflects the whole unfiltered set.
const totalCount = computed(() => total.value)

const totalDuration = computed(() => sums.value?.[activeMode.value] ?? 0)

const pendingCount = computed(
  () => filtered.value.filter((record) => displayStatus(record) === 'pending').length
)
</script>

<template>
  <ElCard shadow="never">
    <ElRow class="py-2" align="middle">
      <ElCol :span="16" :xs="24">
        <ElSegmented v-model="activeMode" :options="modes" aria-label="义工类型切换">
          <template #default="option">
            {{ t('activity.mode.' + option.item + '.name') }}
          </template>
        </ElSegmented>
      </ElCol>
      <ElCol :span="8" :xs="24" class="summary-col" style="text-align: right">
        <span class="text-sm text-gray-500 dark:text-gray-400">
          共 <span class="summary-number">{{ totalCount }}</span> 项 · 累计
          <span class="summary-number">{{ totalDuration.toFixed(1) }}</span> 小时
        </span>
        <ElButton
          type="success"
          :disabled="loading"
          :icon="Refresh"
          text
          bg
          circle
          title="刷新明细"
          aria-label="刷新明细"
          @click="fetchRecords"
        />
      </ElCol>
    </ElRow>
    <div v-if="pendingCount > 0" class="pending-note">
      <ElIcon class="pending-note-icon"><InfoFilled /></ElIcon>
      <span>{{ t('activity.analysis.pendingNote', { count: pendingCount }) }}</span>
    </div>
    <ElResult
      v-if="error && !loading"
      icon="error"
      :title="error"
    />
    <ElTable
      v-else-if="hasRecords || loading"
      v-loading="loading"
      :data="filtered"
      stripe
    >
      <ElTableColumn prop="name" :label="t('activity.form.name')" show-overflow-tooltip />
      <ElTableColumn prop="date" :label="t('activity.form.date')" width="128">
        <template #default="{ row }">
          {{ dayjs(row.date).format('YYYY-MM-DD') }}
        </template>
      </ElTableColumn>
      <ElTableColumn :label="t('activity.form.duration')" width="192">
        <template #default="{ row }">
          <span
            class="duration-cell"
            :class="{ 'duration-cell--pending': displayStatus(row) === 'pending' }"
          >
            <ZActivityDuration
              :mode="row.mine?.mode"
              :duration="row.mine?.duration ?? 0"
              force="short"
            />
          </span>
        </template>
      </ElTableColumn>
      <ElTableColumn :label="t('activity.status.title')" width="96">
        <template #default="{ row }">
          <ZActivityStatus :type="displayStatus(row)" size="small" force="full" />
        </template>
      </ElTableColumn>
      <template #empty>
        <ElEmpty :description="t('activity.mode.' + activeMode + '.name') + '暂无记录'" :image-size="100" />
      </template>
    </ElTable>
    <ElEmpty v-else description="当前类型暂无记录" :image-size="120" />
    <ZPagination
      v-if="!error"
      class="detail-pagination"
      v-model:current-page="page"
      v-model:page-size="perpage"
      :total="totalCount"
      :page-sizes="[10, 20, 50]"
    />
  </ElCard>
</template>

<style scoped>
.summary-col {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

.pending-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 20px;
  color: var(--el-text-color-secondary);
  background-color: var(--el-fill-color-light);
}

.pending-note-icon {
  flex-shrink: 0;
  color: var(--el-color-warning);
}

.duration-cell--pending :deep(.el-button-group .el-button:last-child) {
  color: var(--el-text-color-secondary);
  text-decoration: line-through;
}

.summary-number {
  color: var(--el-text-color-primary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.detail-pagination {
  flex-wrap: wrap;
  row-gap: 8px;
}

.detail-pagination :deep(.el-pagination) {
  flex-wrap: wrap;
  row-gap: 8px;
}
</style>

<script setup lang="ts">
import {
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElPagination,
  ElResult,
  ElRow,
  ElSegmented,
  ElTable,
  ElTableColumn
} from 'element-plus'
import { computed, ref, toRefs, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import dayjs from '@/plugins/dayjs'
import type { Activity, ActivityMember } from '@/../types/v2'
import type { UserActivityTimeSums } from '@/../types'
import { ZActivityDuration, ZActivityStatus } from '@/components'

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
</script>

<template>
  <ElCard shadow="never" v-loading="loading">
    <ElRow class="py-2" align="middle">
      <ElCol :span="16">
        <ElSegmented v-model="activeMode" :options="modes">
          <template #default="option">
            {{ t('activity.mode.' + option.item + '.name') }}
          </template>
        </ElSegmented>
      </ElCol>
      <ElCol :span="8" style="text-align: right">
        <span class="text-sm text-gray-500">共 {{ totalCount }} 项 · 累计 {{ totalDuration.toFixed(1) }} 小时</span>
        <ElButton
          type="success"
          :disabled="loading"
          :icon="Refresh"
          text
          bg
          circle
          @click="fetchRecords"
        />
      </ElCol>
    </ElRow>
    <ElResult
      v-if="error && !loading"
      icon="error"
      :title="error"
    />
    <ElTable v-else-if="hasRecords || loading" :data="filtered" stripe>
      <ElTableColumn prop="name" :label="t('activity.form.name')" show-overflow-tooltip />
      <ElTableColumn prop="date" :label="t('activity.form.date')" width="128">
        <template #default="{ row }">
          {{ dayjs(row.date).format('YYYY-MM-DD') }}
        </template>
      </ElTableColumn>
      <ElTableColumn :label="t('activity.form.duration')" width="192">
        <template #default="{ row }">
          <ZActivityDuration
            :mode="row.mine?.mode"
            :duration="row.mine?.duration ?? 0"
            force="short"
          />
        </template>
      </ElTableColumn>
      <ElTableColumn :label="t('activity.status.title')" width="96">
        <template #default="{ row }">
          <ZActivityStatus :type="row.mine?.status ?? 'effective'" size="small" />
        </template>
      </ElTableColumn>
    </ElTable>
    <ElEmpty v-else description="暂无数据" />
    <ElPagination
      v-if="!error"
      class="py-4"
      v-model:current-page="page"
      v-model:page-size="perpage"
      :total="totalCount"
      layout="total, prev, pager, next, sizes"
      background
      :page-sizes="[10, 20, 50]"
    />
  </ElCard>
</template>

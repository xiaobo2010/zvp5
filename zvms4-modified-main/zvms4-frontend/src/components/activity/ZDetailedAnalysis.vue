<script setup lang="ts">
import {
  ElButton,
  ElCard,
  ElCol,
  ElPagination,
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
  }>(),
  {
    id: '',
    mode: 'on-campus'
  }
)

const { id } = toRefs(props)

const { t } = useI18n()

const modes: ActivityMember['mode'][] = ['on-campus', 'off-campus', 'social-practice']
const activeMode = ref<ActivityMember['mode']>(props.mode)

const records = ref<UserActivity[]>([])
const loading = ref(false)

async function fetchRecords() {
  loading.value = true
  const collected: UserActivity[] = []
  const perpage = 100
  for (let page = 1; page <= 20; page++) {
    const result = await api.user.activity.read(id.value, page, perpage)
    if (!result) break
    collected.push(...result.activities)
    if (collected.length >= result.total || result.activities.length === 0) break
  }
  records.value = collected
  loading.value = false
}

fetchRecords()

watch(id, fetchRecords)

const filtered = computed(() =>
  records.value.filter((record) => record.mine?.mode === activeMode.value)
)

const totalCount = computed(() => filtered.value.length)
const totalDuration = computed(() =>
  filtered.value.reduce((sum, record) => sum + (record.mine?.duration ?? 0), 0)
)

const page = ref(1)
const perpage = ref(10)

const paged = computed(() =>
  filtered.value.slice((page.value - 1) * perpage.value, page.value * perpage.value)
)

watch(
  () => filtered.value.length,
  () => {
    if ((page.value - 1) * perpage.value >= filtered.value.length) page.value = 1
  }
)
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
    <ElTable :data="paged" stripe>
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
    <ElPagination
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
<script setup lang="ts">
import { useRouter } from 'vue-router'
import {
  ElTable,
  ElTableColumn,
  ElCard,
  ElPagination,
  ElInput,
  ElButton,
  ElSwitch,
  ElFormItem,
  ElEmpty,
  ElResult
} from 'element-plus'
import { ref, toRefs, watch } from 'vue'
import api from '@/api'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import type { Group, ActivityTimeRecord } from '@/../types'
import { useWindowSize } from '@vueuse/core'
import { Search } from '@element-plus/icons-vue'

const router = useRouter()
const { height } = useWindowSize()
const { t } = useI18n()
const userStore = useUserStore()

const props = withDefaults(
  defineProps<{
    id: string
  }>(),
  {
    id: ''
  }
)
const group = ref<Group>()
const time = ref<ActivityTimeRecord[]>([])
const page = ref(1)
const perpage = ref(10)
const loading = ref(false)
const error = ref('')
const search = ref('')
const total = ref(0)
const diff = ref(false)
const sort = ref('_id')
const asc = ref(true)

const { id } = toRefs(props)

const tableHeight = ref(height.value * 0.6)

watch(height, () => {
  tableHeight.value = height.value * 0.6
})

async function loadGroup() {
  if (!id.value) {
    group.value = undefined
    return
  }
  try {
    group.value = await api.group.readOne(id.value)
  } catch {
    group.value = undefined
  }
}

watch(id, loadGroup, { immediate: true })

const refresh = async () => {
  error.value = ''
  loading.value = true
  try {
    const res =
      id.value === ''
        ? await api.time.reads(search.value, page.value, perpage.value, sort.value, asc.value)
        : await api.group.reads.time(
            id.value,
            page.value,
            perpage.value,
            search.value,
            false,
            diff.value,
            sort.value,
            asc.value
          )
    if (res) {
      time.value = []
      time.value.push(...res.time)
      total.value = res.size
    } else {
      time.value = []
      total.value = 0
    }
  } catch {
    time.value = []
    total.value = 0
    error.value = '时长列表加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch([id, page, diff], refresh, { immediate: true })

watch(perpage, () => {
  if (page.value !== 1) {
    page.value = 1
  } else {
    refresh()
  }
})

function handleSearch() {
  if (page.value !== 1) {
    page.value = 1
  } else {
    refresh()
  }
}

async function onSortChange(data: {
  column: unknown
  prop: string
  order?: 'ascending' | 'descending'
}) {
  if (data.order) {
    sort.value = data.prop
    asc.value = data.order === 'ascending'
  } else {
    sort.value = '_id'
    asc.value = false
  }
  refresh()
}
</script>

<template>
  <div
    v-if="
      userStore.position.includes('admin') ||
      userStore.position.includes('department') ||
      userStore.groups.includes(id)
    "
  >
    <ElCard shadow="never" v-loading="loading">
      <div style="text-align: right" class="my-2">
        <ElFormItem :label="t('manage.groupDetails.timeList.diff')" style="text-align: right">
          <ElSwitch v-model="diff"></ElSwitch>
        </ElFormItem>
      </div>
      <ElResult v-if="error && !loading" icon="error" :title="error" />
      <ElTable v-else :data="time" stripe :max-height="tableHeight" @sort-change="onSortChange">
        <ElTableColumn prop="name" :label="t('manage.groupDetails.userList.columns.name')" />
        <ElTableColumn prop="id" sortable :label="t('manage.groupDetails.userList.columns.id')" />
        <ElTableColumn prop="on-campus" :label="t('activity.mode.on-campus.short')" v-if="diff">
          <template #default="{ row }">
            <span class="color-green" v-if="row['on-campus'] <= 0">{{
              t('manage.groupDetails.timeList.filled')
            }}</span>
            <span class="color-red" v-else>
              {{
                t('manage.groupDetails.timeList.diffed', { time: row['on-campus'].toFixed(1) })
              }}</span
            >
          </template>
        </ElTableColumn>
        <ElTableColumn prop="on-campus" sortable :label="t('activity.mode.on-campus.short')" v-else>
          <template #default="{ row }">
            <span class="color-green" v-if="row['on-campus'] >= 25">{{
              row['on-campus'].toFixed(1)
            }}</span>
            <span class="color-red" v-else>
              {{ row['on-campus'].toFixed(1) }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="off-campus" :label="t('activity.mode.off-campus.short')" v-if="diff">
          <template #default="{ row }">
            <span class="color-green" v-if="row['off-campus'] <= 0">{{
              t('manage.groupDetails.timeList.filled')
            }}</span>
            <span class="color-red" v-else>
              {{
                t('manage.groupDetails.timeList.diffed', { time: row['off-campus'].toFixed(1) })
              }}</span
            >
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="off-campus"
          sortable
          :label="t('activity.mode.off-campus.short')"
          v-else
        >
          <template #default="{ row }">
            <span class="color-green" v-if="row['off-campus'] >= 15">{{
              row['off-campus'].toFixed(1)
            }}</span>
            <span class="color-red" v-else>{{ row['off-campus'].toFixed(1) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="social-practice"
          :label="t('activity.mode.social-practice.short')"
          v-if="diff"
        >
          <template #default="{ row }">
            <span class="color-green" v-if="row['social-practice'] <= 0">{{
              t('manage.groupDetails.timeList.filled')
            }}</span>
            <span class="color-red" v-else>
              {{
                t('manage.groupDetails.timeList.diffed', {
                  time: row['social-practice'].toFixed(1)
                })
              }}</span
            >
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="social-practice"
          :label="t('activity.mode.social-practice.short')"
          sortable
          v-else
        >
          <template #default="{ row }">
            <span class="color-green" v-if="row['social-practice'] >= 18">{{
              row['social-practice'].toFixed(1)
            }}</span>
            <span class="color-red" v-else>{{ row['social-practice'].toFixed(1) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn fixed="right">
          <template #header>
            <ElInput
              v-model="search"
              size="small"
              :placeholder="t('manage.groupList.columns.search')"
              :prefix-icon="Search"
              @blur="handleSearch"
              @keyup.enter="handleSearch"
            />
          </template>
          <template #default="{ row }">
            <ElButton text bg size="small" @click="router.push(`/user/${row._id}`)">
              {{ t('manage.groupList.columns.details') }}
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <ElEmpty v-if="!loading && !error && time.length === 0" description="暂无数据" />
      <ElPagination
        class="py-1 pt-4"
        v-model:current-page="page"
        v-model:page-size="perpage"
        :total="total"
        :pager-count="3"
        layout="total, prev, pager, next, sizes, jumper"
        background
        :page-sizes="[3, 5, 8, 10, 15, 20]"
      />
    </ElCard>
  </div>
</template>

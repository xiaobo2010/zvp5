<script lang="ts" setup>
import { useRouter } from 'vue-router'
import { ElTable, ElTableColumn, ElCard, ElButton, ElInput } from 'element-plus'
import { nextTick, ref, onMounted, watch } from 'vue'
import type { TableInstance } from 'element-plus'
import api from '@/api'
import type { Group } from '@/../types'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user.ts'
import ZUserPosition from '../tags/ZUserPosition.vue'
import { Search } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const groups = ref<Group[]>([])
const { height } = useWindowSize()
const page = ref(1)
const router = useRouter()
const perpage = ref(10)
const loading = ref(false)
const submission = ref(false)
const search = ref('')
const total = ref(0)
const { t } = useI18n()
const userStore = useUserStore()

const tableRef = ref<TableInstance>()

/** 分页行 + 卡片下边距 + 页面底部留白 */
const BOTTOM_ROOM = 96

/** 表格高度 = 视口高度 − 表格实际顶部 − 底部预留，尽量少留白 */
function computeTableHeight() {
  const el = (tableRef.value as { $el?: HTMLElement } | undefined)?.$el
  const top = el?.getBoundingClientRect?.().top ?? 0
  if (top > 0 && top < height.value * 0.8) {
    return Math.max(280, Math.round(height.value - top - BOTTOM_ROOM))
  }
  return Math.max(280, Math.round(height.value - 320))
}

const tableHeight = ref(computeTableHeight())

watch(height, () => {
  tableHeight.value = computeTableHeight()
})

onMounted(() => {
  nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

function refresh() {
  loading.value = true
  api.group.read_v2(page.value, perpage.value, search.value).then((res) => {
    groups.value = []
    if (!res) return
    groups.value.push(...res.groups)
    total.value = res.size
    loading.value = false
  }).catch(() => {
    loading.value = false
  })
}

onMounted(refresh)
watch(page, refresh)
watch(perpage, () => {
  page.value = 1
  refresh()
})

function handleSearch() {
  page.value = 1
  refresh()
}

async function remove(id: string) {
  submission.value = true
  try {
    await api.group.delete(userStore._id, id)
  } catch (_) {
    submission.value = false
    return
  }
  submission.value = false
  refresh()
}
</script>

<template>
  <div class="px-2">
    <ElCard shadow="never" v-loading="loading">
      <ElTable :ref="tableRef" :data="groups" :max-height="tableHeight">
        <ElTableColumn prop="name" :label="t('manage.groupList.columns.name')"></ElTableColumn>
        <ElTableColumn prop="permission" :label="t('manage.groupList.columns.permission')">
          <template #default="{ row }">
            <ZUserPosition v-for="p in row.permissions ?? []" :key="p" :position="[p]" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="Action">
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
            <ElButton type="primary" size="small" text bg @click="router.push(`/group/${row._id}`)">查看</ElButton>
            <ElButton type="info" size="small" text bg disabled>重命名</ElButton>
            <ElButton type="danger" size="small" text bg :disabled="submission" @click="remove(row._id)">删除</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <ZPagination
        class="py-2"
        v-model:currentPage="page"
        v-model:pageSize="perpage"
        :total="total"
      />
    </ElCard>
  </div>
</template>

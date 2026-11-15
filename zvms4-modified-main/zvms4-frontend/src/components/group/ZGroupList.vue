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
import SkeletonTable from '@/ui/ux/SkeletonTable.vue'
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

// 模板里 `:ref` 收到的是已解包的值，直接绑 ref 对象会被当成 VNodeRef 值传下去；
// 用函数 ref 承接，再写回 tableRef。
function setTableRef(el: unknown) {
  tableRef.value = (el as TableInstance | null) ?? undefined
}

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
    <div class="yg2-toolbar">
      <span class="yg2-toolbar__end">
        <ElInput
          v-model="search"
          size="small"
          class="yg2-toolbar__field"
          :placeholder="t('manage.groupList.columns.search')"
          :prefix-icon="Search"
          clearable
          @blur="handleSearch"
          @keyup.enter="handleSearch"
        />
      </span>
    </div>
    <ElCard shadow="never">
      <SkeletonTable v-if="loading" :rows="6" />
      <ElTable v-else :ref="setTableRef" :data="groups" :max-height="tableHeight">
        <ElTableColumn prop="name" :label="t('manage.groupList.columns.name')" min-width="200" />
        <ElTableColumn
          prop="permission"
          :label="t('manage.groupList.columns.permission')"
          min-width="220"
        >
          <template #default="{ row }">
            <ZUserPosition v-for="p in row.permissions ?? []" :key="p" :position="[p]" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="260" align="right" class-name="col-actions">
          <template #default="{ row }">
            <ElButton class="yg2-action" size="small" @click="router.push(`/group/${row._id}`)">
              查看
            </ElButton>
            <ElButton class="yg2-action yg2-action--info" size="small" disabled>重命名</ElButton>
            <ElButton
              class="yg2-action yg2-action--danger"
              size="small"
              :disabled="submission"
              @click="remove(row._id)"
            >
              删除
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
      <ZPagination
        v-if="!loading"
        class="py-2"
        v-model:currentPage="page"
        v-model:pageSize="perpage"
        :total="total"
      />
    </ElCard>
  </div>
</template>

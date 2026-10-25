<script lang="ts" setup>
import { useRouter } from 'vue-router'
import {
  ElTable,
  ElTableColumn,
  ElCard,
  ElButton,
  ElIcon,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElTooltip
} from 'element-plus'
import { computed, nextTick, ref, onMounted, watch } from 'vue'
import type { TableInstance } from 'element-plus'
import api from '@/api'
import type { Group } from '@/../types'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user.ts'
import ZUserPosition from '../tags/ZUserPosition.vue'
import { Delete, Search, View } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const groups = ref<Group[]>([])
const { width, height } = useWindowSize()
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

/** 窄屏（≤768px，准则 3.7）：操作列收起成图标按钮，搜索框移到表格上方 */
const narrow = computed(() => width.value <= 768)

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

watch([width, height], () => {
  tableHeight.value = computeTableHeight()
})

/** 窄屏工具栏出现/消失会改变表格顶部位置，等布局落定后重算高度 */
watch(narrow, () => {
  void nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
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

/** 删除群组是高危操作：先弹红色确认，说明不可恢复，再走接口 */
async function remove(id: string, name: string) {
  try {
    await ElMessageBox.confirm(
      t('manage.groupList.delete.message', { name }),
      t('manage.groupList.delete.title'),
      {
        type: 'warning',
        confirmButtonText: t('manage.groupList.delete.confirm'),
        cancelButtonText: t('manage.groupList.delete.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  submission.value = true
  try {
    await api.group.delete(userStore._id, id)
  } catch (_) {
    submission.value = false
    return
  }
  submission.value = false
  ElMessage.success(t('manage.groupList.delete.deletedNotice'))
  refresh()
}
</script>

<template>
  <div class="px-2">
    <ElCard shadow="never">
      <!-- 窄屏：搜索框移到表格上方，避免被固定操作列挤出可视区 -->
      <div v-if="narrow" class="group-toolbar">
        <ElInput
          v-model="search"
          size="small"
          clearable
          :placeholder="t('manage.groupList.columns.search')"
          :prefix-icon="Search"
          @blur="handleSearch"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
      </div>

      <!-- 加载遮罩只盖数据区：搜索工具栏与分页保持可交互 -->
      <ElTable ref="tableRef" v-loading="loading" :data="groups" :max-height="tableHeight">
        <ElTableColumn
          prop="name"
          :label="t('manage.groupList.columns.name')"
          :min-width="narrow ? 120 : 160"
          show-overflow-tooltip
        ></ElTableColumn>
        <ElTableColumn
          prop="permission"
          :label="t('manage.groupList.columns.permission')"
          :min-width="narrow ? 100 : 140"
        >
          <template #default="{ row }">
            <ZUserPosition v-for="p in row.permissions ?? []" :key="p" :position="[p]" />
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.groupList.columns.action')"
          :width="narrow ? 112 : 200"
          fixed="right"
        >
          <template #header>
            <ElInput
              v-if="!narrow"
              v-model="search"
              size="small"
              :placeholder="t('manage.groupList.columns.search')"
              :prefix-icon="Search"
              @blur="handleSearch"
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            />
          </template>
          <template #default="{ row }">
            <!-- 宽屏：文字按钮 -->
            <div v-if="!narrow" class="group-actions">
              <ElButton
                type="primary"
                size="small"
                text
                bg
                @click="router.push(`/group/${row._id}`)"
              >
                {{ t('manage.groupList.columns.view') }}
              </ElButton>
              <ElButton type="info" size="small" text bg disabled>
                {{ t('manage.groupList.columns.rename') }}
              </ElButton>
              <ElButton
                type="danger"
                size="small"
                text
                bg
                :disabled="submission"
                @click="remove(row._id, row.name)"
              >
                {{ t('manage.groupList.columns.delete') }}
              </ElButton>
            </div>
            <!-- 窄屏：图标按钮组（44px 触控目标），不依赖横向滚动即可触达 -->
            <div v-else class="group-actions">
              <ElTooltip :content="t('manage.groupList.columns.view')" placement="top">
                <button
                  type="button"
                  class="yg-icon-btn yg-icon-btn--lg group-action--primary"
                  :aria-label="t('manage.groupList.columns.view')"
                  @click="router.push(`/group/${row._id}`)"
                >
                  <ElIcon :size="18"><View /></ElIcon>
                </button>
              </ElTooltip>
              <ElTooltip :content="t('manage.groupList.columns.delete')" placement="top">
                <button
                  type="button"
                  class="yg-icon-btn yg-icon-btn--lg group-action--danger"
                  :aria-label="t('manage.groupList.columns.delete')"
                  :disabled="submission"
                  @click="remove(row._id, row.name)"
                >
                  <ElIcon :size="18"><Delete /></ElIcon>
                </button>
              </ElTooltip>
            </div>
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

<style scoped>
/* 窄屏工具栏：搜索框独占一行，间距走 4px 网格 */
.group-toolbar {
  margin-bottom: var(--yg-space-3);
}

.group-actions {
  display: flex;
  align-items: center;
  gap: var(--yg-space-1);
}

/* 行内图标按钮配色：主色 / 危险色（准则 3.3、5.1） */
.group-action--primary {
  color: var(--yg-color-primary);
}

.group-action--danger {
  color: var(--yg-text-danger);
}

.group-action--danger:hover {
  background-color: var(--yg-color-danger-soft);
}
</style>

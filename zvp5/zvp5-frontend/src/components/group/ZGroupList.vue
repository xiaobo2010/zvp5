<script lang="ts" setup>
import { useRouter } from 'vue-router'
import {
  ElTable,
  ElTableColumn,
  ElCard,
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
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
import { resolveErrorMessage } from '@/utils/errorMessage'
import { Delete, Plus, Search, View } from '@element-plus/icons-vue'
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

async function refresh() {
  loading.value = true
  try {
    const res = await api.group.read_v2(page.value, perpage.value, search.value)
    groups.value = []
    // 空响应也必须收掉 loading：提前 return 会留下永久转圈的表格
    if (!res) return
    groups.value.push(...res.groups)
    total.value = res.size
  } catch {
    // 失败提示由全局拦截器统一负责，这里只需要复位 loading
  } finally {
    loading.value = false
  }
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

/** 添加班级：与平台管理其他弹窗同一范式，只填名称，前后端都要求必填 */
const createVisible = ref(false)
const createName = ref('')
const createError = ref('')
const creating = ref(false)

function openCreate() {
  createName.value = ''
  createError.value = ''
  createVisible.value = true
}

function closeCreate() {
  if (creating.value) {
    return
  }
  createVisible.value = false
}

async function submitCreate() {
  if (creating.value) {
    return
  }
  const name = createName.value.trim()
  if (!name) {
    createError.value = t('manage.groupList.create.required')
    return
  }
  createError.value = ''
  creating.value = true
  try {
    const id = await api.group.createGroupV3(name)
    if (!id) {
      throw new Error('create failed')
    }
    createVisible.value = false
    ElMessage.success(t('manage.groupList.create.createdNotice', { name }))
    if (page.value === 1) {
      refresh()
    } else {
      page.value = 1
    }
  } catch (error) {
    ElMessage.error(resolveErrorMessage(error, 'manage.groupList.create.failed'))
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="px-2">
    <ElCard shadow="never">
      <!-- 工具栏：窄屏搜索框与「添加班级」同一行，宽屏只有按钮且靠右 -->
      <div class="group-toolbar">
        <ElInput
          v-if="narrow"
          v-model="search"
          class="group-toolbar__search"
          size="small"
          clearable
          :placeholder="t('manage.groupList.columns.search')"
          :prefix-icon="Search"
          @blur="handleSearch"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <ElButton class="group-toolbar__add" type="primary" size="small" @click="openCreate">
          <ElIcon class="mr-1"><Plus /></ElIcon>
          {{ t('manage.groupList.create.action') }}
        </ElButton>
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

    <ElDialog
      v-model="createVisible"
      :title="t('manage.groupList.create.title')"
      :close-on-click-modal="!creating"
      :close-on-press-escape="!creating"
      :show-close="!creating"
      width="min(520px, 92vw)"
    >
      <ElForm v-loading="creating" @submit.prevent="submitCreate">
        <ElFormItem
          :label="t('manage.groupList.create.name')"
          :error="createError"
          :required="true"
        >
          <ElInput
            v-model="createName"
            :placeholder="t('manage.groupList.create.placeholder')"
            maxlength="40"
            clearable
            @keyup.enter="submitCreate"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton :disabled="creating" @click="closeCreate">
          {{ t('manage.groupList.create.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="creating" @click="submitCreate">
          {{
            creating
              ? t('manage.groupList.create.creating')
              : t('manage.groupList.create.confirm')
          }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
/* 工具栏：窄屏搜索框与「添加班级」同一行，间距走 4px 网格 */
.group-toolbar {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-3);
}

.group-toolbar__search {
  flex: 1 1 auto;
  min-width: 0;
}

/* 宽屏没有搜索框时按钮靠右，与操作列表头搜索框同一视觉流 */
.group-toolbar__add {
  margin-left: auto;
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

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElDialog,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTooltip
} from 'element-plus'
import type { TableInstance } from 'element-plus'
import { Edit, Refresh, Search } from '@element-plus/icons-vue'
import api from '@/api'
import type { User, UserPosition } from '@/../types'
import { useUserStore } from '@/stores/user'
import ZPagination from '@/components/utils/ZPagination.vue'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'

const { t } = useI18n()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))
const editableRoles: UserPosition[] = ['secretary', 'department', 'admin']

const roleFilter = ref('')
const query = ref('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const users = ref<User[]>([])
const loading = ref(false)
const loadFailed = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const target = ref<User | null>(null)
const checkedRoles = ref<UserPosition[]>([])
const extraRoles = ref<UserPosition[]>([])

const tableRef = ref<TableInstance>()

const { width, height } = useWindowSize()

/** 窄屏（≤768px，准则 3.7）：操作列收起成图标按钮，筛选区改为整行堆叠 */
const narrow = computed(() => width.value <= 768)

/** 窄屏下控件用 large 档，满足 44px 触控目标 */
const controlSize = computed(() => (narrow.value ? 'large' : 'default'))

/** 分页行 + 卡片下边距 + 页面底部留白 */
const BOTTOM_ROOM = 96

/**
 * 表格高度 = 视口高度 − 表格实际顶部 − 底部预留，
 * 以表头真实位置为基准；表格始终带 max-height，表头/内容由 EP 原生同步。
 */
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

watch(narrow, () => {
  void nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

onMounted(() => {
  void nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

const roleOptions = computed(() => [
  { value: '', label: t('manage.roleManagement.filterAll') },
  ...editableRoles.map((role) => ({
    value: role,
    label: t(`manage.roleManagement.roles.${role}`)
  }))
])

function roleLabel(role: string): string {
  return t(`manage.roleManagement.roles.${role}`)
}

function displayRoles(row: User): string[] {
  const roles = (row.roles ?? []) as string[]
  return roles.filter((role) => role !== 'student')
}

function canEdit(row: User): boolean {
  const roles = (row.roles ?? []) as string[]
  return row._id !== userStore._id && !roles.includes('admin')
}

async function loadUsers() {
  if (!isAdmin.value || loading.value) {
    return
  }
  loading.value = true
  try {
    const result = await api.user.read_v2(
      page.value,
      pageSize.value,
      query.value.trim(),
      roleFilter.value
    )
    users.value = result?.users ?? []
    total.value = result?.size ?? 0
    loadFailed.value = false
  } catch {
    users.value = []
    total.value = 0
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  void loadUsers()
}

function changeRoleFilter() {
  page.value = 1
  void loadUsers()
}

function openEdit(row: User) {
  target.value = row
  const roles = (row.roles ?? []) as UserPosition[]
  checkedRoles.value = roles.filter((role) => editableRoles.includes(role))
  extraRoles.value = roles.filter((role) => !editableRoles.includes(role))
  dialogVisible.value = true
}

async function saveRoles() {
  const row = target.value
  if (!row || saving.value) {
    return
  }
  saving.value = true
  try {
    const roles = [...checkedRoles.value, ...extraRoles.value]
    const ok = await api.user.updateRoles(row._id, roles)
    if (!ok) {
      throw new Error('role update failed')
    }
    dialogVisible.value = false
    ElMessage.success(t('manage.roleManagement.saved'))
    await loadUsers()
  } catch {
    ElMessage.error(t('manage.roleManagement.saveFailed'))
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void loadUsers()
})
</script>

<template>
  <div class="roles-page">
    <ElCard v-if="isAdmin" shadow="never" class="roles-card">
      <div class="roles-toolbar">
        <div class="roles-toolbar__title">
          <h2>{{ t('manage.roleManagement.title') }}</h2>
          <p>{{ t('manage.roleManagement.subtitle') }}</p>
        </div>
        <ElButton
          :icon="Refresh"
          text
          bg
          :size="controlSize"
          :loading="loading"
          @click="loadUsers"
        >
          {{ t('manage.roleManagement.refresh') }}
        </ElButton>
      </div>

      <div class="roles-filters">
        <ElSelect
          v-model="roleFilter"
          class="roles-filter"
          :size="controlSize"
          :aria-label="t('manage.roleManagement.filterLabel')"
          @change="changeRoleFilter"
        >
          <ElOption
            v-for="option in roleOptions"
            :key="option.value || 'all'"
            :label="option.label"
            :value="option.value"
          />
        </ElSelect>
        <ElInput
          v-model="query"
          class="roles-search"
          clearable
          :size="controlSize"
          :prefix-icon="Search"
          :placeholder="t('manage.roleManagement.searchPlaceholder')"
          @keyup.enter="search"
          @clear="search"
        />
        <ElButton type="primary" :icon="Search" :size="controlSize" @click="search">
          {{ t('manage.roleManagement.search') }}
        </ElButton>
      </div>

      <ElTable
        ref="tableRef"
        v-loading="loading"
        :data="users"
        stripe
        row-key="_id"
        :max-height="tableHeight"
        class="roles-table"
      >
        <ElTableColumn
          prop="name"
          :label="t('manage.roleManagement.columns.name')"
          :min-width="narrow ? 100 : 120"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="id"
          :label="t('manage.roleManagement.columns.id')"
          :min-width="narrow ? 110 : 120"
          show-overflow-tooltip
        />
        <ElTableColumn
          :label="t('manage.roleManagement.columns.group')"
          :min-width="narrow ? 100 : 120"
        >
          <template #default="{ row }">
            <ZUserGroup
              v-for="group in row.group ?? []"
              :key="group"
              :group="group"
              class="roles-group"
            />
            <span v-if="!(row.group ?? []).length" class="roles-muted">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.roleManagement.columns.roles')"
          :min-width="narrow ? 140 : 180"
        >
          <template #default="{ row }">
            <ElTag
              v-for="role in displayRoles(row)"
              :key="role"
              size="small"
              effect="plain"
              class="roles-tag"
            >
              {{ roleLabel(role) }}
            </ElTag>
            <span v-if="!displayRoles(row).length" class="roles-muted">
              {{ t('manage.roleManagement.roles.student') }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.roleManagement.columns.actions')"
          :width="narrow ? 72 : 140"
          fixed="right"
        >
          <template #default="{ row }">
            <ElButton
              v-if="!narrow"
              text
              bg
              type="primary"
              :icon="Edit"
              :disabled="!canEdit(row)"
              :title="canEdit(row) ? '' : t('manage.roleManagement.cannotEdit')"
              @click="openEdit(row)"
            >
              {{ t('manage.roleManagement.edit') }}
            </ElButton>
            <ElTooltip
              v-else
              :content="
                canEdit(row) ? t('manage.roleManagement.edit') : t('manage.roleManagement.cannotEdit')
              "
              placement="top"
            >
              <span class="roles-action-wrap">
                <button
                  type="button"
                  class="yg-icon-btn yg-icon-btn--lg roles-action"
                  :aria-label="t('manage.roleManagement.edit')"
                  :disabled="!canEdit(row)"
                  @click="openEdit(row)"
                >
                  <ElIcon :size="18"><Edit /></ElIcon>
                </button>
              </span>
            </ElTooltip>
          </template>
        </ElTableColumn>
        <template #empty>
          <span class="roles-muted">
            {{ loadFailed ? t('manage.roleManagement.loadFailed') : t('manage.roleManagement.empty') }}
          </span>
        </template>
      </ElTable>

      <ZPagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        @change="loadUsers"
      />
    </ElCard>

    <ElCard v-else shadow="never">
      <span class="roles-forbidden">{{ t('manage.roleManagement.noPermission') }}</span>
    </ElCard>

    <ElDialog
      v-model="dialogVisible"
      :title="t('manage.roleManagement.dialogTitle', { name: target?.name ?? '' })"
      :close-on-click-modal="!saving"
      :close-on-press-escape="!saving"
      :show-close="!saving"
      width="min(520px, 92vw)"
    >
      <ElForm v-loading="saving">
        <ElFormItem :label="t('manage.roleManagement.columns.roles')">
          <ElCheckboxGroup v-model="checkedRoles" class="roles-checkboxes">
            <ElCheckbox v-for="role in editableRoles" :key="role" :value="role">
              {{ roleLabel(role) }}
            </ElCheckbox>
          </ElCheckboxGroup>
        </ElFormItem>
        <ElFormItem>
          <span class="roles-hint">{{ t('manage.roleManagement.dialogHint') }}</span>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton :disabled="saving" @click="dialogVisible = false">
          {{ t('manage.roleManagement.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="saving" @click="saveRoles">
          {{ saving ? t('manage.roleManagement.saving') : t('manage.roleManagement.save') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.roles-page {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

.roles-card {
  border-radius: var(--yg-radius-lg);
}

.roles-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--yg-space-4);
  margin-bottom: var(--yg-space-4);
}

.roles-toolbar__title h2 {
  margin: 0;
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: 1.4;
  color: var(--yg-text-primary);
}

.roles-toolbar__title p {
  margin: var(--yg-space-1) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.roles-filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-3);
  margin-bottom: var(--yg-space-4);
}

.roles-filter {
  width: 180px;
}

.roles-search {
  flex: 1;
  min-width: 200px;
}

.roles-table {
  width: 100%;
}

.roles-group {
  margin: var(--yg-space-1) var(--yg-space-1) var(--yg-space-1) 0;
}

.roles-tag {
  margin: var(--yg-space-1) var(--yg-space-1) var(--yg-space-1) 0;
}

.roles-muted,
.roles-hint {
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.roles-forbidden {
  color: var(--el-color-danger);
}

/* 行内图标按钮：主色，44px 触控目标 */
.roles-action {
  color: var(--yg-color-primary);
}

/* 禁用态按钮不派发鼠标事件，套一层 span 让 tooltip 仍可提示原因 */
.roles-action-wrap {
  display: inline-flex;
}

.roles-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2) var(--yg-space-4);
}

@media (max-width: 768px) {
  .roles-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .roles-filters {
    flex-direction: column;
    align-items: stretch;
  }

  .roles-filter {
    width: 100%;
  }

  .roles-search {
    min-width: 0;
  }
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
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
        <ElButton :icon="Refresh" text bg :loading="loading" @click="loadUsers">
          {{ t('manage.roleManagement.refresh') }}
        </ElButton>
      </div>

      <div class="roles-filters">
        <ElSelect
          v-model="roleFilter"
          class="roles-filter"
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
          :prefix-icon="Search"
          :placeholder="t('manage.roleManagement.searchPlaceholder')"
          @keyup.enter="search"
          @clear="search"
        />
        <ElButton type="primary" :icon="Search" @click="search">
          {{ t('manage.roleManagement.search') }}
        </ElButton>
      </div>

      <ElTable
        v-loading="loading"
        :data="users"
        stripe
        row-key="_id"
        max-height="60vh"
        class="roles-table"
      >
        <ElTableColumn prop="name" :label="t('manage.roleManagement.columns.name')" min-width="120" />
        <ElTableColumn prop="id" :label="t('manage.roleManagement.columns.id')" min-width="120" />
        <ElTableColumn :label="t('manage.roleManagement.columns.group')" min-width="120">
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
        <ElTableColumn :label="t('manage.roleManagement.columns.roles')" min-width="180">
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
          width="140"
          fixed="right"
        >
          <template #default="{ row }">
            <ElButton
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
  gap: 16px;
}

.roles-card {
  border-radius: 8px;
}

.roles-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.roles-toolbar__title h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--zv-text);
}

.roles-toolbar__title p {
  margin: 4px 0 0;
  color: var(--zv-text-sub);
  font-size: 13px;
}

.roles-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
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
  margin: 2px 4px 2px 0;
}

.roles-tag {
  margin: 2px 4px 2px 0;
}

.roles-muted,
.roles-hint {
  color: var(--zv-text-sub);
  font-size: 13px;
}

.roles-forbidden {
  color: var(--el-color-danger);
}

.roles-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

@media (max-width: 600px) {
  .roles-toolbar {
    flex-direction: column;
  }

  .roles-filter {
    width: 100%;
  }
}
</style>

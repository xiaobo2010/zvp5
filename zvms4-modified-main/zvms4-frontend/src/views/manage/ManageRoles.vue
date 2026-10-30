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
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
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
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import {
  ROLE_FEATURE_KEYS,
  getRolePolicies,
  updateRolePolicy,
  type RoleFeatureKey,
  type RolePoliciesData,
  type RolePolicy,
  type RolePolicyInput,
  type RoleQuotaKind,
  type RoleQuotas
} from '@/api/system/rolePolicies'
import { aiErrorOf } from '@/api/ai/errors'

const { t } = useI18n()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))
const editableRoles: UserPosition[] = ['secretary', 'department', 'admin']

/* ------------------------------------------------------------------ *
 * 二级导航：角色策略（新）/ 用户角色（原有表格）
 * ------------------------------------------------------------------ */

type ManageTab = 'policy' | 'users'

const tab = ref<ManageTab>('policy')

const tabs = computed<Array<{ value: ManageTab; label: string }>>(() => [
  { value: 'policy', label: t('manage.rolePolicies.tabs.policy') },
  { value: 'users', label: t('manage.rolePolicies.tabs.users') }
])

/* ------------------------------------------------------------------ *
 * 角色策略
 * ------------------------------------------------------------------ */

/**
 * 功能开关按模块分组展示；分组只影响排版，能力本身仍由后端 feature_keys 说了算。
 * 顺序与 ROLE_FEATURE_KEYS 一致：AI 能力 → 活动与数据 → 运维只读。
 */
interface FeatureGroup {
  id: 'ai' | 'activity' | 'ops'
  keys: RoleFeatureKey[]
}

const FEATURE_GROUPS: FeatureGroup[] = [
  { id: 'ai', keys: ['ai.chat', 'ai.ocr', 'ai.agent', 'ai.schedule', 'ai.keys'] },
  {
    id: 'activity',
    keys: ['activity.stats', 'attachment.manage', 'export.time', 'import.activity']
  },
  { id: 'ops', keys: ['logs.read', 'notify.publish', 'ban.read', 'quota.manage'] }
]

const QUOTA_KINDS: RoleQuotaKind[] = ['af', 'schedule']

/** 胶囊顺序：团支书 → 学生会 → 学生 → 管理员，其余角色追加在后。 */
const ROLE_PREFERENCE = ['secretary', 'department', 'student', 'admin']

const policyLoading = ref(true)
const policyFailed = ref('')
const policyData = ref<RolePoliciesData | null>(null)
const activeRole = ref('')
const saving = ref(false)
const drafts = ref<Record<string, RolePolicyInput>>({})
const savedDrafts = ref<Record<string, RolePolicyInput>>({})
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

function emptyQuotas(): RoleQuotas {
  return { af: null, schedule: null }
}

function emptyInput(): RolePolicyInput {
  return { features: {}, quotas: emptyQuotas() }
}

/**
 * 缺省的开关按「可用」处理：后端只下发与本角色相关的 key 时，其余能力不该
 * 因为字段缺失就被静默关掉。
 */
function normalizeFeatures(features: Record<string, boolean> | undefined): Record<string, boolean> {
  const result: Record<string, boolean> = {}
  for (const key of ROLE_FEATURE_KEYS) {
    result[key] = features?.[key] !== false
  }
  for (const [key, value] of Object.entries(features ?? {})) {
    if (!(key in result)) {
      result[key] = value !== false
    }
  }
  return result
}

/**
 * 归一化月度配额。
 *
 * 用户侧额度统一成了 `af`；后端若还下发 V051 之前的 `chat` / `ocr`（历史数据
 * 或未迁移的旧接口），一律兜底显示为 `af`，别让页面因为少一个字段就炸掉。
 */
function normalizeQuotas(quotas: Partial<RoleQuotas> | undefined): RoleQuotas {
  const legacy = quotas as (Partial<RoleQuotas> & { chat?: number | null; ocr?: number | null }) | undefined
  return {
    af: quotas?.af ?? legacy?.chat ?? legacy?.ocr ?? null,
    schedule: quotas?.schedule ?? null
  }
}

function toInput(policy: RolePolicy): RolePolicyInput {
  return {
    features: normalizeFeatures(policy.features),
    quotas: normalizeQuotas(policy.quotas)
  }
}

function cloneInput(input: RolePolicyInput): RolePolicyInput {
  return {
    features: { ...input.features },
    quotas: { ...input.quotas }
  }
}

const roleCapsules = computed<string[]>(() => {
  const roles = (policyData.value?.roles ?? []).map((policy) => policy.role)
  const known = ROLE_PREFERENCE.filter((role) => roles.includes(role))
  const extra = roles.filter((role) => !ROLE_PREFERENCE.includes(role))
  return [...known, ...extra]
})

const canEditRole = computed(() => {
  if (!isAdmin.value || !activeRole.value) {
    return false
  }
  return (policyData.value?.editable_roles ?? []).includes(activeRole.value)
})

/** 管理员固定全量权限：这里给一句明确解释，而不是让保存按钮灰着不给理由。 */
const readOnlyHint = computed(() => {
  if (!activeRole.value || canEditRole.value || policyLoading.value) {
    return ''
  }
  return activeRole.value === 'admin'
    ? t('manage.rolePolicies.adminLocked')
    : t('manage.rolePolicies.readOnly')
})

const draft = computed<RolePolicyInput>(
  () => drafts.value[activeRole.value] ?? emptyInput()
)

function featureValue(key: RoleFeatureKey): boolean {
  return draft.value.features[key] !== false
}

function quotaValue(kind: RoleQuotaKind): number | undefined {
  return draft.value.quotas[kind] ?? undefined
}

function inputEquals(a: RolePolicyInput, b: RolePolicyInput): boolean {
  const keys = new Set([...Object.keys(a.features), ...Object.keys(b.features)])
  for (const key of keys) {
    if ((a.features[key] ?? true) !== (b.features[key] ?? true)) {
      return false
    }
  }
  return (
    a.quotas.af === b.quotas.af && a.quotas.schedule === b.quotas.schedule
  )
}

const dirty = computed(() => {
  const baseline = savedDrafts.value[activeRole.value]
  if (!baseline) {
    return false
  }
  return !inputEquals(draft.value, baseline)
})

const policyMeta = computed(() => {
  const policy = (policyData.value?.roles ?? []).find((item) => item.role === activeRole.value)
  if (!policy?.updated_at) {
    return ''
  }
  const time = policy.updated_at.replace('T', ' ').slice(0, 16)
  const parts = [t('manage.rolePolicies.updatedAt', { time })]
  if (policy.updated_by) {
    parts.push(t('manage.rolePolicies.updatedBy', { actor: policy.updated_by }))
  }
  return parts.join(' · ')
})

function policyRoleLabel(role: string): string {
  const key = `manage.rolePolicies.roles.${role}`
  const label = t(key)
  return label === key ? role : label
}

function statusOf(error: unknown): number | null {
  const aiStatus = aiErrorOf(error)?.status
  if (typeof aiStatus === 'number') {
    return aiStatus
  }
  const response = (error as { response?: { status?: unknown } } | null)?.response
  return typeof response?.status === 'number' ? response.status : null
}

/** 403/404 说清楚是权限还是角色不存在；其余的优先回后端 detail 原文。 */
function policyErrorMessage(error: unknown, phase: 'load' | 'save'): string {
  const status = statusOf(error)
  if (status === 403) {
    return t('manage.rolePolicies.forbidden')
  }
  if (status === 404) {
    return t('manage.rolePolicies.notFound')
  }
  if (status === 401) {
    return t('manage.rolePolicies.unauthorized')
  }
  const detail = error instanceof Error ? error.message.trim() : ''
  if (detail) {
    return detail
  }
  return phase === 'load'
    ? t('manage.rolePolicies.loadFailed')
    : t('manage.rolePolicies.saveFailed')
}

/** 把一条策略同时写进列表、草稿与基线（保存成功后回填用）。 */
function applyPolicy(policy: RolePolicy | null | undefined, fallbackRole: string) {
  const normalized: RolePolicy = {
    role: policy?.role || fallbackRole,
    features: policy?.features ?? {},
    quotas: policy?.quotas ?? emptyQuotas(),
    updated_at: policy?.updated_at ?? null,
    updated_by: policy?.updated_by ?? null
  }
  const input = toInput(normalized)
  drafts.value[normalized.role] = input
  savedDrafts.value[normalized.role] = cloneInput(input)
  const list = policyData.value?.roles
  if (list) {
    const index = list.findIndex((item) => item.role === normalized.role)
    if (index >= 0) {
      list[index] = { ...list[index], ...normalized }
    } else {
      list.push(normalized)
    }
  }
}

async function loadPolicies() {
  policyLoading.value = true
  policyFailed.value = ''
  try {
    const data = await getRolePolicies()
    const roles = data.roles ?? []
    policyData.value = {
      roles,
      editable_roles: data.editable_roles ?? [],
      feature_keys: data.feature_keys ?? []
    }
    drafts.value = {}
    savedDrafts.value = {}
    for (const policy of roles) {
      const input = toInput(policy)
      drafts.value[policy.role] = input
      savedDrafts.value[policy.role] = cloneInput(input)
    }
    const present = roles.map((policy) => policy.role)
    if (!present.includes(activeRole.value)) {
      activeRole.value = ROLE_PREFERENCE.find((role) => present.includes(role)) ?? present[0] ?? ''
    }
  } catch (error) {
    policyData.value = null
    drafts.value = {}
    savedDrafts.value = {}
    activeRole.value = ''
    policyFailed.value = policyErrorMessage(error, 'load')
  } finally {
    policyLoading.value = false
  }
}

async function selectRole(role: string) {
  if (role === activeRole.value) {
    return
  }
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        t('manage.rolePolicies.unsavedConfirm'),
        t('manage.rolePolicies.unsavedTitle'),
        {
          type: 'warning',
          confirmButtonText: t('manage.rolePolicies.unsavedOk'),
          cancelButtonText: t('manage.rolePolicies.unsavedCancel'),
          closeOnClickModal: false
        }
      )
    } catch {
      return
    }
  }
  activeRole.value = role
}

function setFeature(key: RoleFeatureKey, value: boolean) {
  const target = drafts.value[activeRole.value]
  if (!target) {
    return
  }
  target.features[key] = value
}

/** ElInputNumber 清空时回 undefined：null 才是「用系统默认」，0 是「不开放」。 */
function setQuota(kind: RoleQuotaKind, value: number | null | undefined) {
  const target = drafts.value[activeRole.value]
  if (!target) {
    return
  }
  target.quotas[kind] = typeof value === 'number' && Number.isFinite(value) ? value : null
}

async function savePolicy() {
  const role = activeRole.value
  const target = drafts.value[role]
  if (!role || !target || !canEditRole.value || saving.value) {
    return
  }
  const dialog = authRef.value
  if (!dialog) {
    ElMessage.error(t('manage.rolePolicies.authUnavailable'))
    return
  }
  const code = await dialog.open(
    t('manage.rolePolicies.authScene', { role: policyRoleLabel(role) })
  )
  if (!code) {
    return
  }
  saving.value = true
  try {
    const updated = await updateRolePolicy(role, cloneInput(target), code)
    // 弹层等请求落地才关闭：失败时保持打开，用户可以直接补一个新动态码
    dialog.succeed()
    applyPolicy(updated, role)
    ElMessage.success(t('manage.rolePolicies.saved'))
  } catch (error) {
    dialog.fail(policyErrorMessage(error, 'save'))
  } finally {
    saving.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 用户角色（原有表格逻辑，行为保持不变）
 * ------------------------------------------------------------------ */

const roleFilter = ref('')
const query = ref('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const users = ref<User[]>([])
const loading = ref(false)
const loadFailed = ref(false)

const dialogVisible = ref(false)
const roleDialogSaving = ref(false)
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
  if (!row || roleDialogSaving.value) {
    return
  }
  roleDialogSaving.value = true
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
    roleDialogSaving.value = false
  }
}

/** 表格首屏高度只在「用户角色」页可见时才量，避免在策略页拿到 0。 */
async function syncTableHeight() {
  await nextTick()
  tableHeight.value = computeTableHeight()
}

watch(tab, (value) => {
  if (value === 'users') {
    void syncTableHeight()
    if (!users.value.length) {
      void loadUsers()
    }
  }
})

onMounted(() => {
  void loadPolicies()
  void loadUsers()
  void syncTableHeight()
})
</script>

<template>
  <div class="roles-page">
    <nav class="roles-tabs" role="tablist" :aria-label="t('manage.rolePolicies.tabsLabel')">
      <button
        v-for="item in tabs"
        :key="item.value"
        type="button"
        role="tab"
        class="roles-tabs__item"
        :class="{ 'is-active': tab === item.value }"
        :data-tab="item.value"
        :aria-selected="tab === item.value"
        @click="tab = item.value"
      >
        {{ item.label }}
      </button>
    </nav>

    <template v-if="tab === 'policy'">
      <ElCard shadow="never" class="roles-card">
        <div class="policy-head">
          <div class="policy-roles" role="group" :aria-label="t('manage.rolePolicies.title')">
            <button
              v-for="role in roleCapsules"
              :key="role"
              type="button"
              class="policy-role"
              :class="{ 'is-active': role === activeRole }"
              :data-role="role"
              :aria-pressed="role === activeRole"
              @click="selectRole(role)"
            >
              {{ policyRoleLabel(role) }}
            </button>
          </div>
          <div class="policy-actions">
            <span v-if="dirty" class="policy-dirty">{{ t('manage.rolePolicies.pending') }}</span>
            <ElButton
              v-if="isAdmin"
              class="policy-save"
              type="primary"
              :loading="saving"
              :disabled="!canEditRole || saving || !dirty"
              @click="savePolicy"
            >
              {{ saving ? t('manage.rolePolicies.saving') : t('manage.rolePolicies.save') }}
            </ElButton>
            <ElButton :icon="Refresh" text bg :loading="policyLoading" @click="loadPolicies">
              {{ t('manage.rolePolicies.refresh') }}
            </ElButton>
          </div>
        </div>

        <p v-if="readOnlyHint" class="policy-notice">{{ readOnlyHint }}</p>
        <p v-if="policyMeta" class="policy-meta">{{ policyMeta }}</p>
      </ElCard>

      <ElCard v-if="policyLoading" shadow="never" class="roles-card">
        <p class="policy-state">{{ t('manage.rolePolicies.loading') }}</p>
      </ElCard>

      <ElCard v-else-if="policyFailed" shadow="never" class="roles-card">
        <div class="policy-state">
          <p class="policy-state__error">{{ policyFailed }}</p>
          <ElButton :loading="policyLoading" @click="loadPolicies">
            {{ t('manage.rolePolicies.refresh') }}
          </ElButton>
        </div>
      </ElCard>

      <ElCard v-else-if="!roleCapsules.length" shadow="never" class="roles-card">
        <p class="policy-state">{{ t('manage.rolePolicies.empty') }}</p>
      </ElCard>

      <template v-else>
        <ElCard shadow="never" class="roles-card">
          <div class="policy-section__head">
            <h2 class="policy-section__title">{{ t('manage.rolePolicies.sections.features') }}</h2>
            <p class="policy-section__hint">
              {{ t('manage.rolePolicies.sections.featuresHint') }}
            </p>
          </div>

          <div class="policy-groups">
            <section v-for="group in FEATURE_GROUPS" :key="group.id" class="policy-group">
              <h3 class="policy-group__title">
                {{ t(`manage.rolePolicies.featureGroups.${group.id}`) }}
              </h3>
              <div
                v-for="key in group.keys"
                :key="key"
                class="policy-row"
                :data-feature="key"
              >
                <div class="policy-row__text">
                  <span class="policy-row__name">
                    {{ t(`manage.rolePolicies.features.${key}.label`) }}
                  </span>
                  <span class="policy-row__hint">
                    {{ t(`manage.rolePolicies.features.${key}.hint`) }}
                  </span>
                </div>
                <ElSwitch
                  :model-value="featureValue(key)"
                  :disabled="!canEditRole || saving"
                  @update:model-value="(value) => setFeature(key, value === true)"
                />
              </div>
            </section>
          </div>
        </ElCard>

        <ElCard shadow="never" class="roles-card">
          <div class="policy-section__head">
            <h2 class="policy-section__title">{{ t('manage.rolePolicies.sections.quotas') }}</h2>
            <p class="policy-section__hint">{{ t('manage.rolePolicies.sections.quotasHint') }}</p>
          </div>

          <div class="policy-quotas">
            <div
              v-for="kind in QUOTA_KINDS"
              :key="kind"
              class="policy-row policy-row--quota"
              :data-quota="kind"
            >
              <div class="policy-row__text">
                <span class="policy-row__name">
                  {{ t(`manage.rolePolicies.quotas.${kind}.label`) }}
                </span>
                <span class="policy-row__hint">
                  {{ t(`manage.rolePolicies.quotas.${kind}.hint`) }}
                </span>
              </div>
              <ElInputNumber
                class="policy-quota__input"
                :model-value="quotaValue(kind)"
                :min="0"
                :step="1"
                :controls="false"
                :disabled="!canEditRole || saving"
                :placeholder="t('manage.rolePolicies.quotaPlaceholder')"
                @update:model-value="(value) => setQuota(kind, value)"
              />
            </div>
          </div>
        </ElCard>
      </template>
    </template>

    <template v-else>
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
                  canEdit(row)
                    ? t('manage.roleManagement.edit')
                    : t('manage.roleManagement.cannotEdit')
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
              {{
                loadFailed ? t('manage.roleManagement.loadFailed') : t('manage.roleManagement.empty')
              }}
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
    </template>

    <ElDialog
      v-model="dialogVisible"
      :title="t('manage.roleManagement.dialogTitle', { name: target?.name ?? '' })"
      :close-on-click-modal="!roleDialogSaving"
      :close-on-press-escape="!roleDialogSaving"
      :show-close="!roleDialogSaving"
      width="min(520px, 92vw)"
    >
      <ElForm v-loading="roleDialogSaving">
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
        <ElButton :disabled="roleDialogSaving" @click="dialogVisible = false">
          {{ t('manage.roleManagement.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="roleDialogSaving" @click="saveRoles">
          {{
            roleDialogSaving ? t('manage.roleManagement.saving') : t('manage.roleManagement.save')
          }}
        </ElButton>
      </template>
    </ElDialog>

    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
.roles-page {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

/* 二级导航：与 ManageHome 的胶囊导航同一观感，只是少了图标 */
.roles-tabs {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1);
  overflow-x: auto;
  border-radius: var(--yg-radius-pill);
  background-color: var(--yg-bg-surface-sunken);
  scrollbar-width: none;
}

.roles-tabs::-webkit-scrollbar {
  display: none;
}

.roles-tabs__item {
  flex: 0 0 auto;
  min-height: var(--yg-control-height-lg);
  padding: 0 var(--yg-space-4);
  border: none;
  border-radius: var(--yg-radius-pill);
  background-color: transparent;
  color: var(--yg-text-tertiary);
  font-family: inherit;
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-snug);
  white-space: nowrap;
  cursor: pointer;
  transition:
    color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out);
}

.roles-tabs__item:hover {
  color: var(--yg-text-primary);
  background-color: var(--yg-bg-hover);
}

.roles-tabs__item:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.roles-tabs__item.is-active {
  color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
  font-weight: var(--yg-font-weight-medium);
}

.roles-card {
  border-radius: var(--yg-radius-lg);
}

/* ---- 角色策略：头部 ---- */

.policy-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-4);
  flex-wrap: wrap;
}

.policy-roles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.policy-role {
  min-height: var(--yg-control-height-lg);
  padding: 0 var(--yg-space-4);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-pill);
  background-color: var(--yg-bg-surface);
  color: var(--yg-text-secondary);
  font-family: inherit;
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-snug);
  cursor: pointer;
  transition:
    color var(--yg-dur-base) var(--yg-ease-out),
    border-color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out);
}

.policy-role:hover {
  color: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
}

.policy-role:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.policy-role.is-active {
  color: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
  background-color: var(--yg-color-primary-soft);
  font-weight: var(--yg-font-weight-medium);
}

.policy-actions {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-left: auto;
}

.policy-dirty {
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-sm);
}

.policy-notice {
  margin: var(--yg-space-3) 0 0;
  padding: var(--yg-space-2) var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background-color: var(--yg-bg-surface-sunken);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.policy-meta {
  margin: var(--yg-space-2) 0 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

/* ---- 角色策略：分区 ---- */

.policy-section__head {
  margin-bottom: var(--yg-space-4);
}

.policy-section__title {
  margin: 0;
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  color: var(--yg-text-primary);
}

.policy-section__hint {
  margin: var(--yg-space-1) 0 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
}

.policy-groups {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

.policy-group__title {
  margin: 0 0 var(--yg-space-2);
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-weight: var(--yg-font-weight-medium);
  letter-spacing: 0.04em;
}

.policy-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-4);
  padding: var(--yg-space-3) 0;
  border-top: 1px solid var(--yg-border-subtle);
}

.policy-group__title + .policy-row {
  border-top: none;
}

.policy-row__text {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-width: 0;
}

.policy-row__name {
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-snug);
}

.policy-row__hint {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
}

.policy-quotas {
  display: flex;
  flex-direction: column;
}

.policy-quota__input {
  flex: none;
  width: 140px;
}

.policy-state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--yg-space-3);
  padding: var(--yg-space-4) 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
}

.policy-state__error {
  margin: 0;
  color: var(--yg-text-danger);
}

/* ---- 用户角色（原有表格） ---- */

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
  color: var(--yg-text-danger);
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
  .policy-head {
    flex-direction: column;
    align-items: stretch;
  }

  .policy-actions {
    margin-left: 0;
    justify-content: flex-end;
  }

  .policy-quota__input {
    width: 100%;
  }

  .policy-row--quota {
    flex-direction: column;
    align-items: stretch;
  }

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

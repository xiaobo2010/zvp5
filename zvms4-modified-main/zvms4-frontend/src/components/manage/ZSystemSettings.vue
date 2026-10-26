<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElEmpty,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTooltip
} from 'element-plus'
import { Check, Delete, InfoFilled, Lock, Plus, Refresh, RefreshLeft } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import {
  listSettings,
  settingErrorMessage,
  settingErrorStatus,
  updateSettings,
  type SettingValue,
  type SystemSettingItem
} from '@/api/system/settings'
import {
  addRoleRow,
  allowsFreeEntry,
  changedKeys,
  computeChanges,
  differsFromDefault,
  draftFromValue,
  flatDictMin,
  flatDictRows,
  formatSettingValue,
  groupSections,
  removeRoleRow,
  resolveControlKind,
  roleOptions,
  roleQuotaColumns,
  type FlatDictRow,
  type FormatWords,
  type RoleQuotaRow,
  type SettingDraftValue
} from './settingsLogic'

const { t } = useI18n()

const items = ref<SystemSettingItem[]>([])
const defaults = ref<Record<string, SettingValue>>({})
/** 草稿值：界面上正在编辑的值，与存储值分开，改坏了可以整体丢弃。 */
const drafts = reactive<Record<string, SettingDraftValue>>({})

const loading = ref(false)
const loadFailed = ref(false)
const forbidden = ref(false)
const saving = ref(false)

const { width } = useWindowSize()

/** 窄屏（≤768px，准则 3.7）：表单单列堆叠，控件放大到 44px 触控目标 */
const narrow = computed(() => width.value <= 768)
const controlSize = computed(() => (narrow.value ? 'large' : 'default'))

const sections = computed(() => groupSections(items.value))
const changes = computed(() => computeChanges(items.value, drafts))
const pendingKeys = computed(() => new Set(changedKeys(items.value, drafts)))
const hasChanges = computed(() => changes.value.length > 0)

const words = computed<FormatWords>(() => ({
  on: t('manage.settings.on'),
  off: t('manage.settings.off'),
  empty: t('manage.settings.emptyValue')
}))

function sectionTitle(group: string): string {
  if (group === 'ai') {
    return t('manage.settings.groups.ai')
  }
  if (group === 'ops') {
    return t('manage.settings.groups.ops')
  }
  return t('manage.settings.groups.other')
}

/** 角色枚举的显示名：认识的就翻成中文，不认识的原样显示。 */
function roleLabel(role: string): string {
  const key = `manage.settings.roles.${role}`
  const label = t(key)
  return label === key ? role : label
}

/** 额度能力列的显示名，同上。 */
function kindLabel(kind: string): string {
  const key = `manage.settings.roleQuota.kinds.${kind}`
  const label = t(key)
  return label === key ? kind : label
}

function resetDrafts() {
  for (const key of Object.keys(drafts)) {
    delete drafts[key]
  }
  for (const item of items.value) {
    drafts[item.key] = draftFromValue(item, item.value)
  }
}

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  loadFailed.value = false
  forbidden.value = false
  try {
    const data = await listSettings()
    items.value = Array.isArray(data?.items) ? data.items : []
    defaults.value = data?.defaults ?? {}
    resetDrafts()
  } catch (error) {
    items.value = []
    if (settingErrorStatus(error) === 403) {
      forbidden.value = true
    } else {
      loadFailed.value = true
    }
  } finally {
    loading.value = false
  }
}

/* ---------------- 各类型的取值读写 ---------------- */

function numberValue(item: SystemSettingItem): number {
  const draft = drafts[item.key]
  return typeof draft === 'number' ? draft : 0
}

function boolValue(item: SystemSettingItem): boolean {
  const draft = drafts[item.key]
  return typeof draft === 'boolean' ? draft : false
}

function textValue(item: SystemSettingItem): string {
  const draft = drafts[item.key]
  return typeof draft === 'string' ? draft : ''
}

function listValue(item: SystemSettingItem): string[] {
  const draft = drafts[item.key]
  return Array.isArray(draft) ? (draft as string[]) : []
}

function rowsOf(item: SystemSettingItem): RoleQuotaRow[] {
  const draft = drafts[item.key]
  return Array.isArray(draft) ? (draft as RoleQuotaRow[]) : []
}

function columnsOf(item: SystemSettingItem): string[] {
  return roleQuotaColumns(rowsOf(item))
}

/** 扁平 dict（{ 能力: 次数 }）的行。 */
function flatRowsOf(item: SystemSettingItem): FlatDictRow[] {
  const draft = drafts[item.key]
  return Array.isArray(draft) ? (draft as FlatDictRow[]) : []
}

/** 只有角色×能力表才需要独占整行；扁平 dict 就两列，挤在控件列里正好。 */
function isWideRow(item: SystemSettingItem): boolean {
  return resolveControlKind(item) === 'roleQuota'
}

/** 扁平 dict：值是数字，画「键 → 数字」。 */
function isFlatDict(item: SystemSettingItem): boolean {
  return resolveControlKind(item) === 'flatDict'
}

function updateFlatValue(item: SystemSettingItem, key: string, value: number | undefined) {
  const row = flatRowsOf(item).find((entry) => entry.key === key)
  if (row) {
    row.value = typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : 0
  }
}

function updateNumber(item: SystemSettingItem, value: number | undefined) {
  drafts[item.key] = typeof value === 'number' && Number.isFinite(value) ? value : (item.min ?? 0)
}

function updateBool(item: SystemSettingItem, value: boolean | string | number) {
  drafts[item.key] = value === true || value === 'true' || value === 1
}

function updateText(item: SystemSettingItem, value: string) {
  drafts[item.key] = value ?? ''
}

function updateList(item: SystemSettingItem, value: string[]) {
  drafts[item.key] = Array.isArray(value) ? value : []
}

function updateRole(item: SystemSettingItem, index: number, role: string) {
  const rows = rowsOf(item)
  const row = rows[index]
  if (row) {
    row.role = role
  }
}

function updateLimit(item: SystemSettingItem, index: number, kind: string, value: number | undefined) {
  const row = rowsOf(item)[index]
  if (!row) {
    return
  }
  row.limits[kind] = typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function addRow(item: SystemSettingItem) {
  const rows = rowsOf(item)
  drafts[item.key] = addRoleRow(rows, roleQuotaColumns(rows))
}

function removeRow(item: SystemSettingItem, index: number) {
  drafts[item.key] = removeRoleRow(rowsOf(item), index)
}

/* ---------------- 次要信息 ---------------- */

function isChangedFromDefault(item: SystemSettingItem): boolean {
  return differsFromDefault(item, defaults.value)
}

function defaultText(item: SystemSettingItem): string {
  const value = defaults.value[item.key]
  if (value === undefined) {
    return ''
  }
  return formatSettingValue(value, words.value)
}

/** 悬浮提示里带配置键与最后修改信息，避免每项都堆一行小字。 */
function itemMetaText(item: SystemSettingItem): string {
  const parts = [item.key]
  const time = item.updated_at ? dayjs(item.updated_at).format('YYYY-MM-DD HH:mm') : ''
  if (time && item.updated_by) {
    parts.push(t('manage.settings.updatedAt', { time, actor: item.updated_by }))
  } else if (time) {
    parts.push(t('manage.settings.updatedAtOnly', { time }))
  } else if (item.updated_by) {
    parts.push(t('manage.settings.updatedBy', { actor: item.updated_by }))
  }
  return parts.join(' · ')
}

function hasMeta(item: SystemSettingItem): boolean {
  return Boolean(item.updated_at || item.updated_by)
}

/* ---------------- 保存 ---------------- */

function changeSummaryLines() {
  const current = changes.value
  return [
    h(
      'p',
      { style: { margin: '0 0 8px', color: 'var(--yg-text-secondary)', fontSize: '13px' } },
      t('manage.settings.confirm.summary', { count: current.length })
    ),
    ...current.map((change) =>
      h(
        'div',
        {
          style: {
            display: 'flex',
            gap: '8px',
            alignItems: 'baseline',
            flexWrap: 'wrap',
            fontSize: '13px',
            lineHeight: '1.7'
          }
        },
        [
          h(
            'span',
            { style: { color: 'var(--yg-text-primary)', fontWeight: '500' } },
            change.label
          ),
          h(
            'span',
            { style: { color: 'var(--yg-text-tertiary)' } },
            formatSettingValue(change.from, words.value)
          ),
          h('span', { style: { color: 'var(--yg-text-tertiary)' } }, '→'),
          h(
            'span',
            { style: { color: 'var(--yg-color-primary)' } },
            formatSettingValue(change.to, words.value)
          )
        ]
      )
    )
  ]
}

async function save() {
  if (saving.value || !hasChanges.value) {
    return
  }
  const pending = changes.value
  if (!pending.length) {
    return
  }
  saving.value = true
  try {
    await ElMessageBox.confirm(
      h(
        'div',
        { style: { maxHeight: '320px', overflowY: 'auto' } },
        changeSummaryLines()
      ),
      t('manage.settings.confirm.title'),
      {
        confirmButtonText: t('manage.settings.confirm.ok'),
        cancelButtonText: t('manage.settings.confirm.cancel'),
        type: 'warning',
        closeOnClickModal: false
      }
    )
  } catch {
    saving.value = false
    return
  }

  try {
    await updateSettings(pending.map((change) => ({ key: change.key, value: change.to })))
    ElMessage.success(t('manage.settings.saved'))
    await load()
  } catch (error) {
    if (settingErrorStatus(error) === 403) {
      forbidden.value = true
    }
    ElMessage.error(settingErrorMessage(error, t('manage.settings.saveFailed')))
  } finally {
    saving.value = false
  }
}

/** 丢弃未保存的改动，回到服务器上的值。 */
function discard() {
  resetDrafts()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="settings-page">
    <!-- 工具栏：不参与加载遮罩，刷新与保存始终可点 -->
    <header class="settings-toolbar">
      <div class="settings-toolbar__title">
        <h2>{{ t('manage.settings.title') }}</h2>
        <p>{{ t('manage.settings.subtitle') }}</p>
      </div>
      <div class="settings-toolbar__actions">
        <ElButton
          v-if="hasChanges"
          :icon="RefreshLeft"
          :size="controlSize"
          :disabled="saving"
          @click="discard"
        >
          {{ t('manage.settings.discard') }}
        </ElButton>
        <ElButton
          :icon="Refresh"
          :size="controlSize"
          :loading="loading"
          :disabled="saving"
          @click="load"
        >
          {{ t('manage.settings.refresh') }}
        </ElButton>
        <ElButton
          type="primary"
          :icon="Check"
          :size="controlSize"
          :loading="saving"
          :disabled="!hasChanges"
          @click="save"
        >
          {{ t('manage.settings.save') }}
        </ElButton>
      </div>
    </header>

    <p v-if="hasChanges" class="settings-pending">
      {{ t('manage.settings.pendingCount', { count: changes.length }) }}
    </p>

    <!-- 403：说清楚是权限问题，不给空白页 -->
    <section v-if="forbidden" class="yg-card">
      <div class="yg-card__body">
        <div class="settings-forbidden">
          <div class="settings-forbidden__icon">
            <ElIcon :size="24"><Lock /></ElIcon>
          </div>
          <h3 class="settings-forbidden__title">{{ t('manage.settings.forbidden.title') }}</h3>
          <p class="settings-forbidden__desc">{{ t('manage.settings.forbidden.desc') }}</p>
        </div>
      </div>
    </section>

    <section v-else-if="loadFailed" class="yg-card">
      <div class="yg-card__body">
        <ElEmpty :description="t('manage.settings.loadFailed')" :image-size="90">
          <template #extra>
            <ElButton :size="controlSize" @click="load">{{ t('manage.settings.refresh') }}</ElButton>
          </template>
        </ElEmpty>
      </div>
    </section>

    <!-- 首次加载：骨架屏占位，避免出现空白页 -->
    <section v-else-if="loading && !items.length" class="yg-card">
      <div class="yg-card__body">
        <div class="yg-skeleton is-active">
          <div v-for="line in 4" :key="line" class="yg-skeleton__text" />
        </div>
      </div>
    </section>

    <section v-else-if="!items.length" class="yg-card">
      <div class="yg-card__body">
        <ElEmpty :description="t('manage.settings.empty')" :image-size="90" />
      </div>
    </section>

    <!-- 分区：遮罩只盖内容区，分区标题与工具栏始终可见 -->
    <section v-for="section in sections" :key="section.group" class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-card__title">{{ sectionTitle(section.group) }}</h2>
        <span class="yg-card__extra">{{ t('manage.settings.itemCount', { count: section.items.length }) }}</span>
      </header>
      <div v-loading="loading" class="yg-card__body settings-group">
        <div
          v-for="item in section.items"
          :key="item.key"
          class="setting-row"
          :class="{
            'is-pending': pendingKeys.has(item.key),
            'is-wide': isWideRow(item)
          }"
        >
          <div class="setting-row__info">
            <div class="setting-row__head">
              <span class="setting-row__label">{{ item.label }}</span>
              <span
                v-if="pendingKeys.has(item.key)"
                class="yg-tag yg-tag--sky setting-row__tag"
              >
                {{ t('manage.settings.pending') }}
              </span>
              <span
                v-if="isChangedFromDefault(item)"
                class="yg-tag yg-tag--outline setting-row__tag"
              >
                {{ t('manage.settings.changed', { value: defaultText(item) }) }}
              </span>
              <ElTooltip
                v-if="hasMeta(item)"
                :content="itemMetaText(item)"
                placement="top"
              >
                <span
                  class="setting-row__meta"
                  tabindex="0"
                  :aria-label="itemMetaText(item)"
                >
                  <ElIcon :size="14"><InfoFilled /></ElIcon>
                </span>
              </ElTooltip>
            </div>
            <p v-if="item.description" class="setting-row__desc">{{ item.description }}</p>
            <p v-if="item.type === 'int' && (item.min !== null || item.max !== null)" class="setting-row__hint">
              {{ t('manage.settings.range', { min: item.min ?? '—', max: item.max ?? '—' }) }}
            </p>
          </div>

          <div class="setting-row__control">
            <!-- int -->
            <ElInputNumber
              v-if="item.type === 'int'"
              :model-value="numberValue(item)"
              :min="item.min ?? undefined"
              :max="item.max ?? undefined"
              :size="controlSize"
              class="w-full"
              @update:model-value="(value: number | undefined) => updateNumber(item, value)"
            />

            <!-- bool -->
            <ElSwitch
              v-else-if="item.type === 'bool'"
              :model-value="boolValue(item)"
              :size="controlSize"
              :active-text="t('manage.settings.on')"
              :inactive-text="t('manage.settings.off')"
              @update:model-value="(value: boolean | string | number) => updateBool(item, value)"
            />

            <!-- str -->
            <ElInput
              v-else-if="item.type === 'str'"
              :model-value="textValue(item)"
              :size="controlSize"
              clearable
              @update:model-value="(value: string) => updateText(item, value)"
            />

            <!-- list：多选；没有候选值时允许自填 -->
            <ElSelect
              v-else-if="item.type === 'list'"
              :model-value="listValue(item)"
              :size="controlSize"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              :multiple-limit="-1"
              default-first-option
              :allow-create="allowsFreeEntry(item)"
              class="w-full"
              @update:model-value="(value: string[]) => updateList(item, value)"
            >
              <ElOption
                v-for="option in item.options ?? []"
                :key="option"
                :label="roleLabel(option)"
                :value="option"
              />
            </ElSelect>

            <!-- dict（扁平）：{ 能力: 次数 }，键由后端固定，只改值 -->
            <div v-else-if="isFlatDict(item)" class="flat-dict">
              <div
                v-for="row in flatRowsOf(item)"
                :key="`${item.key}-${row.key}`"
                class="flat-dict__row"
              >
                <span class="flat-dict__key">{{ kindLabel(row.key) }}</span>
                <ElInputNumber
                  :model-value="row.value"
                  :min="flatDictMin(item)"
                  :max="item.max ?? undefined"
                  :precision="0"
                  :step="1"
                  :size="controlSize"
                  controls-position="right"
                  class="w-full"
                  @update:model-value="(value: number | undefined) => updateFlatValue(item, row.key, value)"
                />
              </div>
              <p v-if="!flatRowsOf(item).length" class="flat-dict__empty">
                {{ t('manage.settings.flatDict.empty') }}
              </p>
            </div>

            <!-- dict（嵌套）：角色额度表，可增删角色行 -->
            <div v-else-if="item.type === 'dict'" class="role-quota">
              <div class="yg-table__wrapper role-quota__wrapper">
                <table class="yg-table yg-table--stripe role-quota__table">
                  <thead>
                    <tr>
                      <th class="role-quota__col-role">
                        {{ t('manage.settings.roleQuota.role') }}
                      </th>
                      <th v-for="kind in columnsOf(item)" :key="kind" class="role-quota__col-kind">
                        {{ kindLabel(kind) }}
                      </th>
                      <th class="role-quota__col-action">
                        {{ t('manage.settings.roleQuota.actions') }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, index) in rowsOf(item)" :key="`${item.key}-${index}`">
                      <td class="role-quota__col-role">
                        <ElSelect
                          :model-value="row.role"
                          :size="controlSize"
                          filterable
                          default-first-option
                          :allow-create="true"
                          class="w-full"
                          @update:model-value="(value: string) => updateRole(item, index, value)"
                        >
                          <ElOption
                            v-for="option in roleOptions(item)"
                            :key="option"
                            :label="roleLabel(option)"
                            :value="option"
                          />
                        </ElSelect>
                      </td>
                      <td v-for="kind in columnsOf(item)" :key="kind" class="role-quota__col-kind">
                        <ElInputNumber
                          :model-value="row.limits[kind]"
                          :min="0"
                          :size="controlSize"
                          controls-position="right"
                          class="w-full"
                          @update:model-value="(value: number | undefined) => updateLimit(item, index, kind, value)"
                        />
                      </td>
                      <td class="role-quota__col-action">
                        <button
                          type="button"
                          class="yg-icon-btn"
                          :aria-label="t('manage.settings.roleQuota.removeRow')"
                          :title="t('manage.settings.roleQuota.removeRow')"
                          @click="removeRow(item, index)"
                        >
                          <ElIcon :size="16"><Delete /></ElIcon>
                        </button>
                      </td>
                    </tr>
                    <tr v-if="!rowsOf(item).length">
                      <td :colspan="columnsOf(item).length + 2" class="role-quota__empty">
                        {{ t('manage.settings.roleQuota.empty') }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ElButton
                :icon="Plus"
                :size="controlSize"
                plain
                class="role-quota__add"
                @click="addRow(item)"
              >
                {{ t('manage.settings.roleQuota.addRow') }}
              </ElButton>
            </div>

            <ElInput
              v-else
              :model-value="textValue(item)"
              :size="controlSize"
              clearable
              @update:model-value="(value: string) => updateText(item, value)"
            />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

/* ---------- 工具栏（不参与遮罩） ---------- */
.settings-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--yg-space-4);
  flex-wrap: wrap;
}

.settings-toolbar__title h2 {
  margin: 0;
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: 1.4;
  color: var(--yg-text-primary);
}

.settings-toolbar__title p {
  margin: var(--yg-space-1) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.settings-toolbar__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

/* 待保存提示：正常状态，用主色而不是警告色 */
.settings-pending {
  margin: 0;
  color: var(--yg-color-primary);
  font-size: var(--yg-font-size-sm);
}

/* ---------- 分组内的条目 ---------- */
.settings-group {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

.setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 300px);
  gap: var(--yg-space-4);
  align-items: start;
  padding-bottom: var(--yg-space-4);
  border-bottom: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
}

.setting-row:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

/* 角色额度表列数多，独占整行：挤在右侧控件列里会被压到只剩横向滚动 */
.setting-row.is-wide {
  grid-template-columns: minmax(0, 1fr);
}

.setting-row__info {
  min-width: 0;
}

.setting-row__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.setting-row__label {
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.setting-row__tag {
  height: 22px;
}

.setting-row__meta {
  display: inline-flex;
  align-items: center;
  color: var(--yg-text-tertiary);
  cursor: help;
}

.setting-row__meta:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
  border-radius: var(--yg-radius-xs);
}

.setting-row__desc {
  margin: var(--yg-space-1) 0 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  line-height: 1.6;
}

.setting-row__hint {
  margin: var(--yg-space-1) 0 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
}

.setting-row__control {
  min-width: 0;
}

/* ---------- 扁平 dict：键 → 数字 ---------- */
.flat-dict {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}

.flat-dict__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 160px;
  align-items: center;
  gap: var(--yg-space-3);
}

.flat-dict__key {
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-secondary);
}

.flat-dict__empty {
  margin: 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
}

/* ---------- 角色额度表 ---------- */
.role-quota {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}

.role-quota__wrapper {
  max-width: 100%;
}

.role-quota__table {
  min-width: 460px;
}

/* 首列固定：窄屏横向滚动时角色名不被推出视野 */
.role-quota__table th.role-quota__col-role,
.role-quota__table td.role-quota__col-role {
  position: sticky;
  left: 0;
  z-index: 1;
  background-color: var(--yg-bg-surface);
  min-width: 140px;
}

.role-quota__table th.role-quota__col-role {
  z-index: 2;
  background-color: var(--yg-bg-surface-sunken);
}

.role-quota__table tbody tr:nth-child(even) td.role-quota__col-role {
  background-color: var(--yg-bg-surface-sunken);
}

.role-quota__col-kind {
  min-width: 110px;
}

.role-quota__col-action {
  width: 52px;
  text-align: center;
}

.role-quota__empty {
  padding: var(--yg-space-4);
  text-align: center;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
}

.role-quota__add {
  align-self: flex-start;
}

/* ---------- 403 / 空态 ---------- */
.settings-forbidden {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-6) var(--yg-space-4);
  text-align: center;
}

.settings-forbidden__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--yg-space-10);
  height: var(--yg-space-10);
  border-radius: 50%;
  background-color: var(--yg-bg-surface-sunken);
  color: var(--yg-text-tertiary);
}

.settings-forbidden__title {
  margin: 0;
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.settings-forbidden__desc {
  margin: 0;
  max-width: 420px;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: 1.7;
}

/* ---------- 窄屏：表单单列堆叠 ---------- */
@media (max-width: 768px) {
  .setting-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--yg-space-3);
  }

  .settings-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .settings-toolbar__actions {
    justify-content: flex-start;
  }
}
</style>

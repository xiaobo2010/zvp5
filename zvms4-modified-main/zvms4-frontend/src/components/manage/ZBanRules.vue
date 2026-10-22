<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElDialog,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessageBox,
  ElMessage,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import type { FormInstance, FormItemRule, FormRules } from 'element-plus'
import {
  createRule,
  deleteRule,
  getBannedUsers,
  getRules,
  getTriggers,
  getViolations,
  updateRule,
  type BannedUser,
  type BanRule,
  type BanTrigger,
  type BanViolation
} from '@/api/ban'
import ZPagination from '@/components/utils/ZPagination.vue'

const { t } = useI18n()

const rules = ref<BanRule[]>([])
const rulesLoading = ref(false)
const rulesFailed = ref(false)

const triggers = ref<BanTrigger[]>([])
const triggersFailed = ref(false)

const violations = ref<BanViolation[]>([])
const violationsLoading = ref(false)
const violationsFailed = ref(false)
const violationsPage = ref(1)
const violationsPerpage = ref(20)
const violationsTotal = ref(0)
const violationsQuery = ref('')

const users = ref<BannedUser[]>([])
const usersLoading = ref(false)
const usersFailed = ref(false)
const usersPage = ref(1)
const usersPerpage = ref(20)
const usersTotal = ref(0)
const usersQuery = ref('')

const dialogVisible = ref(false)
const saving = ref(false)
const editingCode = ref('')
const editingName = ref('')
const formRef = ref<FormInstance>()
const form = reactive({
  threshold: 3,
  window_hours: 24,
  penaltiesText: '1,2,7'
})

const createDialogVisible = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({
  name: '',
  code: '',
  trigger: '',
  threshold: 3,
  window_hours: 24,
  penaltiesText: '1,3,7'
})

const PERMANENT_TOKENS = ['0']

function splitPenalties(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .filter((part) => part.length > 0)
}

// 0 是"永久"的文本记号:只允许出现在末位且只能出现一次
function hasMisplacedPermanent(text: string): boolean {
  const parts = splitPenalties(text)
  const positions = parts.reduce<number[]>((acc, part, index) => {
    if (PERMANENT_TOKENS.includes(part)) {
      acc.push(index)
    }
    return acc
  }, [])
  if (positions.length === 0) {
    return false
  }
  return positions.length > 1 || positions[0] !== parts.length - 1
}

function parsePenalties(text: string): (number | null)[] | null {
  const parts = splitPenalties(text)
  if (parts.length < 1 || parts.length > 6) {
    return null
  }
  if (hasMisplacedPermanent(text)) {
    return null
  }
  const lastIndex = parts.length - 1
  const parsed: (number | null)[] = []
  for (let i = 0; i < parts.length; i++) {
    const token = parts[i]!
    if (i === lastIndex && PERMANENT_TOKENS.includes(token)) {
      parsed.push(null)
      continue
    }
    const days = Number(token)
    if (!Number.isInteger(days) || days <= 0) {
      return null
    }
    const previous = parsed[parsed.length - 1]
    if (previous !== undefined && (previous === null || days <= previous)) {
      return null
    }
    parsed.push(days)
  }
  return parsed
}

function formatPenaltiesInput(penalties: (number | null)[]): string {
  return penalties.map((days) => (days === null ? '0' : String(days))).join(',')
}

function validateThreshold(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (value === undefined || value === null) {
    callback(new Error(t('manage.banRules.validation.thresholdRequired')))
    return
  }
  const days = Number(value)
  if (!Number.isInteger(days) || days < 2 || days > 20) {
    callback(new Error(t('manage.banRules.validation.thresholdRange')))
    return
  }
  callback()
}

function validateWindow(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (value === undefined || value === null) {
    callback(new Error(t('manage.banRules.validation.windowRequired')))
    return
  }
  const hours = Number(value)
  if (!Number.isInteger(hours) || hours < 1 || hours > 168) {
    callback(new Error(t('manage.banRules.validation.windowRange')))
    return
  }
  callback()
}

function validatePenalties(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const text = typeof value === 'string' ? value : ''
  if (hasMisplacedPermanent(text)) {
    callback(new Error(t('manage.banRules.validation.penaltiesPermanentPosition')))
    return
  }
  if (parsePenalties(text) === null) {
    callback(new Error(t('manage.banRules.validation.penaltiesFormat')))
    return
  }
  callback()
}

function validateNameRequired(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (typeof value !== 'string' || value.trim().length === 0) {
    callback(new Error(t('manage.banRules.rule.nameRequired')))
    return
  }
  callback()
}

const CODE_PATTERN = /^[a-z][a-z0-9_]{2,63}$/

function validateCode(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (typeof value !== 'string' || value.length === 0) {
    callback(new Error(t('manage.banRules.rule.codeRequired')))
    return
  }
  if (!CODE_PATTERN.test(value)) {
    callback(new Error(t('manage.banRules.rule.codeFormat')))
    return
  }
  callback()
}

function validateTriggerRequired(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  if (typeof value !== 'string' || value.length === 0) {
    callback(new Error(t('manage.banRules.rule.triggerRequired')))
    return
  }
  callback()
}

const formRules: FormRules = {
  threshold: [{ validator: validateThreshold, trigger: 'blur' }] as FormItemRule[],
  window_hours: [{ validator: validateWindow, trigger: 'blur' }] as FormItemRule[],
  penaltiesText: [{ validator: validatePenalties, trigger: 'blur' }] as FormItemRule[]
}

const createFormRules: FormRules = {
  name: [{ validator: validateNameRequired, trigger: 'blur' }] as FormItemRule[],
  code: [{ validator: validateCode, trigger: 'blur' }] as FormItemRule[],
  trigger: [{ validator: validateTriggerRequired, trigger: 'change' }] as FormItemRule[],
  threshold: [{ validator: validateThreshold, trigger: 'blur' }] as FormItemRule[],
  window_hours: [{ validator: validateWindow, trigger: 'blur' }] as FormItemRule[],
  penaltiesText: [{ validator: validatePenalties, trigger: 'blur' }] as FormItemRule[]
}

async function loadTriggers() {
  try {
    triggers.value = await getTriggers()
    triggersFailed.value = false
  } catch {
    triggers.value = []
    triggersFailed.value = true
  }
}

async function loadRules() {
  rulesLoading.value = true
  try {
    rules.value = await getRules()
    rulesFailed.value = false
  } catch {
    rules.value = []
    rulesFailed.value = true
  } finally {
    rulesLoading.value = false
  }
}

async function loadUsers() {
  usersLoading.value = true
  try {
    const result = await getBannedUsers(
      usersPage.value,
      usersPerpage.value,
      usersQuery.value.trim()
    )
    users.value = result.users
    usersTotal.value = result.size
    usersFailed.value = false
  } catch {
    users.value = []
    usersFailed.value = true
  } finally {
    usersLoading.value = false
  }
}

onMounted(() => {
  void loadRules()
  void loadUsers()
})

async function loadViolations() {
  violationsLoading.value = true
  try {
    const result = await getViolations(
      violationsPage.value,
      violationsPerpage.value,
      violationsQuery.value.trim()
    )
    violations.value = result.violations
    violationsTotal.value = result.size
    violationsFailed.value = false
  } catch {
    violations.value = []
    violationsFailed.value = true
  } finally {
    violationsLoading.value = false
  }
}

onMounted(() => {
  void loadRules()
  void loadTriggers()
  void loadViolations()
  void loadUsers()
})

watch(violationsPage, () => {
  void loadViolations()
})

watch(violationsPerpage, () => {
  if (violationsPage.value === 1) {
    void loadViolations()
  } else {
    violationsPage.value = 1
  }
})

function searchViolations() {
  if (violationsPage.value === 1) {
    void loadViolations()
  } else {
    violationsPage.value = 1
  }
}

const KNOWN_SIGNALS = ['iframe', 'bridge', 'devtools']

function signalLabel(signal: string): string {
  // 未注册的新信号直接展示原始值,避免在模板里渲染一串 i18n key
  if (!KNOWN_SIGNALS.includes(signal)) {
    return signal
  }
  return t(`manage.banRules.violations.signals.${signal}`)
}

const selectedTrigger = computed(() =>
  triggers.value.find((item) => item.trigger === createForm.trigger)
)

function onTriggerChange(value: string | number | boolean | object) {
  const meta = triggers.value.find((item) => item.trigger === value)
  if (!meta) {
    return
  }
  if (meta.config?.threshold !== undefined) {
    createForm.threshold = meta.config.threshold
  }
  if (meta.config?.window_hours !== undefined) {
    createForm.window_hours = meta.config.window_hours
  }
}

function triggerDefaultHint(): string {
  const threshold = selectedTrigger.value?.config?.threshold ?? 2
  const windowHours = selectedTrigger.value?.config?.window_hours ?? 1
  return t('manage.banRules.rule.triggerHint', {
    threshold,
    window_hours: windowHours
  })
}

function triggerTitle(rule: BanRule): string {
  return rule.trigger || ''
}

watch(usersPage, () => {
  void loadUsers()
})

watch(usersPerpage, () => {
  if (usersPage.value === 1) {
    void loadUsers()
  } else {
    usersPage.value = 1
  }
})

function searchUsers() {
  if (usersPage.value === 1) {
    void loadUsers()
  } else {
    usersPage.value = 1
  }
}

function replaceRule(updated: BanRule) {
  const index = rules.value.findIndex((item) => item.code === updated.code)
  if (index >= 0) {
    rules.value[index] = updated
  }
}

async function toggleRule(rule: BanRule, enabled: string | number | boolean) {
  const next = Boolean(enabled)
  if (rule.enabled === next) {
    return
  }
  const previous = rule.enabled
  rule.enabled = next
  try {
    const updated = await updateRule(rule.code, { enabled: next })
    replaceRule(updated)
  } catch {
    rule.enabled = previous
    ElMessage.error(t('manage.banRules.revertedNotice'))
  }
}

function openEdit(rule: BanRule) {
  editingCode.value = rule.code
  editingName.value = rule.name
  form.threshold = rule.config?.threshold ?? 2
  form.window_hours = rule.config?.window_hours ?? 1
  form.penaltiesText = formatPenaltiesInput(rule.penalties ?? [])
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

async function submitEdit() {
  if (!formRef.value) {
    return
  }
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }
  if (hasMisplacedPermanent(form.penaltiesText)) {
    ElMessage.error(t('manage.banRules.validation.penaltiesPermanentPosition'))
    return
  }
  const penalties = parsePenalties(form.penaltiesText)
  if (penalties === null) {
    ElMessage.error(t('manage.banRules.validation.penaltiesFormat'))
    return
  }
  saving.value = true
  try {
    const updated = await updateRule(editingCode.value, {
      config: { threshold: form.threshold, window_hours: form.window_hours },
      penalties
    })
    replaceRule(updated)
    dialogVisible.value = false
    ElMessage.success(t('manage.banRules.savedNotice'))
  } catch {
    ElMessage.error(t('manage.banRules.saveFailed'))
  } finally {
    saving.value = false
  }
}

function openCreate() {
  createForm.name = ''
  createForm.code = ''
  createForm.trigger = ''
  createForm.threshold = 3
  createForm.window_hours = 24
  createForm.penaltiesText = '1,3,7'
  if (triggers.value.length === 0) {
    void loadTriggers()
  }
  createDialogVisible.value = true
  void nextTick(() => createFormRef.value?.clearValidate())
}

async function submitCreate() {
  if (!createFormRef.value) {
    return
  }
  const valid = await createFormRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }
  const penalties = parsePenalties(createForm.penaltiesText)
  if (penalties === null) {
    ElMessage.error(t('manage.banRules.validation.penaltiesFormat'))
    return
  }
  creating.value = true
  try {
    await createRule({
      code: createForm.code,
      name: createForm.name.trim(),
      trigger: createForm.trigger,
      config: { threshold: createForm.threshold, window_hours: createForm.window_hours },
      penalties,
      enabled: true
    })
    createDialogVisible.value = false
    ElMessage.success(t('manage.banRules.rule.createdNotice'))
    void loadRules()
  } catch {
    ElMessage.error(t('manage.banRules.rule.createFailed'))
  } finally {
    creating.value = false
  }
}

async function removeRule(rule: BanRule) {
  try {
    await ElMessageBox.confirm(
      t('manage.banRules.rule.deleteConfirm', { name: rule.name }),
      t('manage.banRules.rule.delete')
    )
  } catch {
    return
  }
  try {
    await deleteRule(rule.code)
    ElMessage.success(t('manage.banRules.rule.deletedNotice'))
    void loadRules()
  } catch (err) {
    // 内置规则被并发删除/409 拒绝时给出专门文案
    const message = (err as { message?: string }).message
    if (rule.builtin || (message ?? '').includes('内置')) {
      ElMessage.error(t('manage.banRules.rule.deleteBuiltinDenied'))
    } else {
      ElMessage.error(t('manage.banRules.rule.deleteFailed'))
    }
  }
}

function formatTrigger(rule: BanRule): string {  const threshold = rule.config?.threshold
  const windowHours = rule.config?.window_hours
  if (threshold === undefined || windowHours === undefined) {
    return '—'
  }
  return t('manage.banRules.rule.triggerText', { threshold, window_hours: windowHours })
}

function formatPenalties(rule: BanRule): string {
  if (!rule.penalties?.length) {
    return '—'
  }
  return rule.penalties
    .map((days) =>
      days === null
        ? t('manage.banRules.rule.penaltyPermanent')
        : t('manage.banRules.rule.penaltyDay', { days })
    )
    .join(' → ')
}

function formatTime(value?: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const fill = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${fill(date.getMonth() + 1)}-${fill(date.getDate())} ${fill(date.getHours())}:${fill(date.getMinutes())}`
}

function formatUntil(user: BannedUser): string {
  if (user.permanent) {
    return t('manage.banRules.users.permanent')
  }
  return formatTime(user.until)
}

function formatSource(user: BannedUser): string {
  if (user.source === 'admin') {
    return t('manage.banRules.users.sourceAdmin')
  }
  if (user.source === 'rule' && user.rule_code) {
    const rule = rules.value.find((item) => item.code === user.rule_code)
    const label = rule?.name || user.rule_code
    return t('manage.banRules.users.sourceRule', { code: label })
  }
  // 老数据封禁功能上线前就处于封禁状态:source/rule_code 为 null,归为历史封禁
  return t('manage.banRules.users.sourceUnknown')
}

function formatReason(user: BannedUser): string | null {
  return user.reason && user.reason.length > 0 ? user.reason : null
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never" v-loading="rulesLoading">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <span>{{ t('manage.banRules.rulesTitle') }}</span>
          <div class="flex items-center gap-2">
            <ElButton type="primary" size="small" @click="openCreate">
              {{ t('manage.banRules.rule.create') }}
            </ElButton>
            <ElButton text size="small" @click="loadRules">
              {{ t('manage.banRules.refresh') }}
            </ElButton>
          </div>
        </div>
      </template>
      <ElTable :data="rules" row-key="code">
        <ElTableColumn
          prop="name"
          :label="t('manage.banRules.rule.columns.name')"
          min-width="180"
        >
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <span>{{ row.name }}</span>
              <ElTag v-if="row.builtin" size="small" type="info">
                {{ t('manage.banRules.rule.builtin') }}
              </ElTag>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="trigger_name"
          :label="t('manage.banRules.rule.columns.trigger')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="block truncate" :title="triggerTitle(row)">
              {{ row.trigger_name || row.trigger || '—' }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="config"
          :label="t('manage.banRules.rule.columns.config')"
          min-width="170"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatTrigger(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="penalties"
          :label="t('manage.banRules.rule.columns.penalties')"
          min-width="220"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatPenalties(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="enabled"
          :label="t('manage.banRules.rule.columns.enabled')"
          width="90"
          align="center"
        >
          <template #default="{ row }">
            <ElSwitch
              :model-value="row.enabled"
              @change="(value: string | number | boolean) => toggleRule(row, value)"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn fixed="right" width="150" align="right">
          <template #default="{ row }">
            <div class="flex items-center justify-end gap-1">
              <ElButton text type="primary" size="small" @click="openEdit(row)">
                {{ t('manage.banRules.rule.edit') }}
              </ElButton>
              <span :title="row.builtin ? t('manage.banRules.rule.deleteBuiltinDenied') : undefined">
                <ElButton
                  text
                  type="danger"
                  size="small"
                  :disabled="row.builtin"
                  @click="removeRule(row)"
                >
                  {{ t('manage.banRules.rule.delete') }}
                </ElButton>
              </span>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="rulesFailed ? t('manage.banRules.loadFailed') : t('manage.banRules.empty.rules')"
            :image-size="100"
          />
        </template>
      </ElTable>
      <p class="text-xs" style="color: var(--zv-label)">
        {{ t('manage.banRules.rule.penaltiesHint') }}
      </p>
    </ElCard>

    <ElCard shadow="never" v-loading="violationsLoading">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <span>{{ t('manage.banRules.violations.title') }}</span>
          <div class="flex items-center gap-2">
            <ElInput
              v-model="violationsQuery"
              :placeholder="t('manage.banRules.violations.searchPlaceholder')"
              clearable
              class="w-56"
              @keyup.enter="searchViolations"
              @clear="searchViolations"
            />
            <ElButton size="small" @click="searchViolations">
              {{ t('manage.banRules.violations.search') }}
            </ElButton>
          </div>
        </div>
      </template>
      <ElTable :data="violations" row-key="_id" stripe>
        <ElTableColumn
          prop="user_name"
          :label="t('manage.banRules.violations.columns.name')"
          min-width="100"
        />
        <ElTableColumn
          prop="user_id"
          :label="t('manage.banRules.violations.columns.userId')"
          min-width="100"
        />
        <ElTableColumn
          prop="trigger"
          :label="t('manage.banRules.violations.columns.type')"
          min-width="150"
        >
          <template #default="{ row }">
            <span>{{ row.trigger_name || row.trigger || '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="signal"
          :label="t('manage.banRules.violations.columns.signal')"
          min-width="120"
        >
          <template #default="{ row }">
            <span>{{ signalLabel(row.signal) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="detail"
          :label="t('manage.banRules.violations.columns.detail')"
          min-width="200"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-if="row.detail">{{ row.detail }}</span>
            <span v-else class="text-xs" style="color: var(--zv-label)">
              {{ t('manage.banRules.violations.detailUnknown') }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="created_at"
          :label="t('manage.banRules.violations.columns.time')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatTime(row.created_at) }}</span>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="
              violationsFailed
                ? t('manage.banRules.loadFailed')
                : t('manage.banRules.violations.empty')
            "
            :image-size="100"
          />
        </template>
      </ElTable>
      <ZPagination
        v-model:current-page="violationsPage"
        v-model:page-size="violationsPerpage"
        :total="violationsTotal"
      />
    </ElCard>

    <ElCard shadow="never" v-loading="usersLoading">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <span>{{ t('manage.banRules.usersTitle') }}</span>
          <div class="flex items-center gap-2">
            <ElInput
              v-model="usersQuery"
              :placeholder="t('manage.banRules.users.searchPlaceholder')"
              clearable
              class="w-56"
              @keyup.enter="searchUsers"
              @clear="searchUsers"
            />
            <ElButton size="small" @click="searchUsers">
              {{ t('manage.banRules.users.search') }}
            </ElButton>
          </div>
        </div>
      </template>
      <ElTable :data="users" row-key="oid" stripe>
        <ElTableColumn
          prop="name"
          :label="t('manage.banRules.users.columns.name')"
          min-width="100"
        />
        <ElTableColumn
          prop="user_id"
          :label="t('manage.banRules.users.columns.userId')"
          min-width="100"
        />
        <ElTableColumn
          prop="class_name"
          :label="t('manage.banRules.users.columns.className')"
          min-width="110"
        />
        <ElTableColumn
          prop="reason"
          :label="t('manage.banRules.users.columns.reason')"
          min-width="200"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-if="formatReason(row)">{{ row.reason }}</span>
            <span v-else class="text-xs" style="color: var(--zv-label)">
              {{ t('manage.banRules.users.reasonUnknown') }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="until"
          :label="t('manage.banRules.users.columns.period')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatUntil(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="source"
          :label="t('manage.banRules.users.columns.source')"
          min-width="150"
        >
          <template #default="{ row }">
            <span>{{ formatSource(row) }}</span>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="
              usersFailed ? t('manage.banRules.loadFailed') : t('manage.banRules.users.empty')
            "
            :image-size="100"
          />
        </template>
      </ElTable>
      <ZPagination
        v-model:current-page="usersPage"
        v-model:page-size="usersPerpage"
        :total="usersTotal"
      />
    </ElCard>

    <ElDialog
      v-model="dialogVisible"
      :title="t('manage.banRules.dialog.title', { name: editingName })"
      width="min(480px, 92vw)"
    >
      <ElForm
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="right"
        label-width="140px"
      >
        <ElFormItem :label="t('manage.banRules.dialog.thresholdLabel')" prop="threshold">
          <ElInputNumber v-model="form.threshold" :min="2" :max="20" :step="1" step-strictly />
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.dialog.windowLabel')" prop="window_hours">
          <ElInputNumber v-model="form.window_hours" :min="1" :max="168" :step="1" step-strictly />
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.dialog.penaltiesLabel')" prop="penaltiesText">
          <ElInput
            v-model="form.penaltiesText"
            :placeholder="t('manage.banRules.dialog.penaltiesPlaceholder')"
          />
          <p class="text-xs w-full" style="color: var(--zv-label)">
            {{ t('manage.banRules.dialog.penaltiesHint') }}
          </p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">
          {{ t('manage.banRules.dialog.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="saving" @click="submitEdit">
          {{ t('manage.banRules.dialog.save') }}
        </ElButton>
      </template>
    </ElDialog>

    <ElDialog
      v-model="createDialogVisible"
      :title="t('manage.banRules.rule.create')"
      width="min(480px, 92vw)"
    >
      <ElForm
        ref="createFormRef"
        :model="createForm"
        :rules="createFormRules"
        label-position="right"
        label-width="140px"
      >
        <ElFormItem :label="t('manage.banRules.rule.nameLabel')" prop="name">
          <ElInput v-model="createForm.name" />
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.rule.codeLabel')" prop="code">
          <ElInput v-model="createForm.code" :placeholder="t('manage.banRules.rule.codePlaceholder')" />
          <p class="text-xs w-full" style="color: var(--zv-label)">
            {{ t('manage.banRules.rule.codeHint') }}
          </p>
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.rule.triggerLabel')" prop="trigger">
          <ElSelect v-model="createForm.trigger" @update:model-value="onTriggerChange">
            <ElOption
              v-for="item in triggers"
              :key="item.trigger"
              :label="item.name"
              :value="item.trigger"
            />
          </ElSelect>
          <template v-if="selectedTrigger">
            <p class="text-xs w-full" style="color: var(--zv-label)">
              {{ selectedTrigger.description }}
            </p>
            <p class="text-xs w-full" style="color: var(--zv-label)">
              {{ triggerDefaultHint() }}
            </p>
          </template>
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.dialog.thresholdLabel')" prop="threshold">
          <ElInputNumber v-model="createForm.threshold" :min="2" :max="20" :step="1" step-strictly />
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.dialog.windowLabel')" prop="window_hours">
          <ElInputNumber v-model="createForm.window_hours" :min="1" :max="168" :step="1" step-strictly />
        </ElFormItem>
        <ElFormItem :label="t('manage.banRules.dialog.penaltiesLabel')" prop="penaltiesText">
          <ElInput
            v-model="createForm.penaltiesText"
            :placeholder="t('manage.banRules.dialog.penaltiesPlaceholder')"
          />
          <p class="text-xs w-full" style="color: var(--zv-label)">
            {{ t('manage.banRules.dialog.penaltiesHint') }}
          </p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="createDialogVisible = false">
          {{ t('manage.banRules.dialog.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="creating" @click="submitCreate">
          {{ t('manage.banRules.dialog.create') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

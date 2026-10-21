<script setup lang="ts">
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
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
  ElMessage,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import type { FormInstance, FormItemRule, FormRules } from 'element-plus'
import { getBannedUsers, getRules, updateRule, type BannedUser, type BanRule } from '@/api/ban'
import ZPagination from '@/components/utils/ZPagination.vue'

const { t } = useI18n()

const rules = ref<BanRule[]>([])
const rulesLoading = ref(false)
const rulesFailed = ref(false)

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

function parsePenalties(text: string): number[] | null {
  const parts = text
    .split(/[,，\s]+/)
    .filter((part) => part.length > 0)
    .map((part) => Number(part))
  if (parts.length < 1 || parts.length > 6) {
    return null
  }
  if (parts.some((days) => !Number.isInteger(days) || days <= 0)) {
    return null
  }
  for (let i = 1; i < parts.length; i++) {
    if (parts[i]! <= parts[i - 1]!) {
      return null
    }
  }
  return parts
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

function validatePenalties(_rule: unknown, _value: unknown, callback: (error?: Error) => void) {
  if (parsePenalties(form.penaltiesText) === null) {
    callback(new Error(t('manage.banRules.validation.penaltiesFormat')))
    return
  }
  callback()
}

const formRules: FormRules = {
  threshold: [{ validator: validateThreshold, trigger: 'blur' }] as FormItemRule[],
  window_hours: [{ validator: validateWindow, trigger: 'blur' }] as FormItemRule[],
  penaltiesText: [{ validator: validatePenalties, trigger: 'blur' }] as FormItemRule[]
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
  form.penaltiesText = (rule.penalties ?? []).join(',')
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

function formatTrigger(rule: BanRule): string {
  const threshold = rule.config?.threshold
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
    .map((days) => t('manage.banRules.rule.penaltyDay', { days }))
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
          <ElButton text size="small" @click="loadRules">
            {{ t('manage.banRules.refresh') }}
          </ElButton>
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
          prop="config"
          :label="t('manage.banRules.rule.columns.trigger')"
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
        <ElTableColumn fixed="right" width="90" align="right">
          <template #default="{ row }">
            <ElButton text type="primary" size="small" @click="openEdit(row)">
              {{ t('manage.banRules.rule.edit') }}
            </ElButton>
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
  </div>
</template>

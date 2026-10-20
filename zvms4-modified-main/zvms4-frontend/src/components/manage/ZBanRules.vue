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
import { getRecords, getRules, updateRule, type BanRecord, type BanRule } from '@/api/ban'
import ZPagination from '@/components/utils/ZPagination.vue'

const { t } = useI18n()

const rules = ref<BanRule[]>([])
const rulesLoading = ref(false)
const rulesFailed = ref(false)

const records = ref<BanRecord[]>([])
const recordsLoading = ref(false)
const recordsFailed = ref(false)
const recordsPage = ref(1)
const recordsPerpage = ref(20)
const recordsTotal = ref(0)

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

async function loadRecords() {
  recordsLoading.value = true
  try {
    const result = await getRecords(recordsPage.value, recordsPerpage.value)
    records.value = result.records
    recordsTotal.value = result.size
    recordsFailed.value = false
  } catch {
    records.value = []
    recordsFailed.value = true
  } finally {
    recordsLoading.value = false
  }
}

onMounted(() => {
  void loadRules()
  void loadRecords()
})

watch(recordsPage, () => {
  void loadRecords()
})

watch(recordsPerpage, () => {
  if (recordsPage.value === 1) {
    void loadRecords()
  } else {
    recordsPage.value = 1
  }
})

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

function formatExpires(record: BanRecord): string {
  if (record.permanent) {
    return t('manage.banRules.records.permanent')
  }
  return formatTime(record.expires_at)
}

function formatSource(record: BanRecord): string {
  if (record.source === 'admin') {
    return t('manage.banRules.records.sourceAdmin')
  }
  const rule = rules.value.find((item) => item.code === record.rule_code)
  if (rule) {
    return `${t('manage.banRules.records.sourceRule')}:${rule.name}`
  }
  return record.rule_code || t('manage.banRules.records.sourceRule')
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

    <ElCard shadow="never" v-loading="recordsLoading">
      <template #header>
        <span>{{ t('manage.banRules.recordsTitle') }}</span>
      </template>
      <ElTable :data="records" row-key="_id" stripe>
        <ElTableColumn
          prop="user_name"
          :label="t('manage.banRules.records.columns.name')"
          min-width="100"
        />
        <ElTableColumn
          prop="user_id"
          :label="t('manage.banRules.records.columns.userId')"
          min-width="100"
        />
        <ElTableColumn
          prop="source"
          :label="t('manage.banRules.records.columns.source')"
          min-width="160"
        >
          <template #default="{ row }">
            <span>{{ formatSource(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="reason"
          :label="t('manage.banRules.records.columns.reason')"
          min-width="220"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="started_at"
          :label="t('manage.banRules.records.columns.startedAt')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatTime(row.started_at) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="expires_at"
          :label="t('manage.banRules.records.columns.expiresAt')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatExpires(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="operator_name"
          :label="t('manage.banRules.records.columns.operator')"
          min-width="100"
        />
        <template #empty>
          <ElEmpty
            :description="
              recordsFailed ? t('manage.banRules.loadFailed') : t('manage.banRules.empty.records')
            "
            :image-size="100"
          />
        </template>
      </ElTable>
      <ZPagination
        v-model:current-page="recordsPage"
        v-model:page-size="recordsPerpage"
        :total="recordsTotal"
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

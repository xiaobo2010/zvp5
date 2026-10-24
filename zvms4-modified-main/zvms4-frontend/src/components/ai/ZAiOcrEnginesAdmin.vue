<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElDialog,
  ElEmpty,
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
  ElTag
} from 'element-plus'
import { Delete, Edit, Plus, Refresh } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import {
  aiErrorCodeOf,
  aiErrorCopy,
  createOcrEngine,
  deleteOcrEngine,
  listOcrEngines,
  updateOcrEngine
} from '@/api/ai'
import type { AiOcrEngineKind, AiOcrEngineRecord } from '@/../types/ai'

const { t } = useI18n()

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{1,39}$/
const PRIORITY_MIN = 0
const PRIORITY_MAX = 999

/** 每种引擎类型要求的凭证字段（与后端 services/ai/ocr_engines_admin.py 一致）。 */
const SECRET_FIELDS: Record<AiOcrEngineKind, string[]> = {
  tencent: ['secret_id', 'secret_key', 'region'],
  aliyun: ['access_key_id', 'access_key_secret'],
  baidu: ['api_key', 'secret_key'],
  rapidocr: [],
  groq: ['api_key']
}

const SECRET_LABELS: Record<string, string> = {
  secret_id: 'ai.engines.secretId',
  secret_key: 'ai.engines.secretKey',
  region: 'ai.engines.region',
  access_key_id: 'ai.engines.accessKeyId',
  access_key_secret: 'ai.engines.accessKeySecret',
  api_key: 'ai.engines.apiKey'
}

const KIND_OPTIONS: AiOcrEngineKind[] = ['tencent', 'aliyun', 'baidu', 'rapidocr', 'groq']

const rows = ref<AiOcrEngineRecord[]>([])
const loading = ref(false)
const loadFailed = ref(false)
const deletingId = ref('')

type DialogMode = 'create' | 'edit'
const mode = ref<DialogMode>('create')
const dialogVisible = ref(false)
const editingId = ref('')
const submitting = ref(false)
const formRef = ref<FormInstance>()

const form = reactive({
  name: '',
  kind: 'rapidocr' as AiOcrEngineKind,
  priority: 100,
  enabled: true,
  config: '',
  secret: {} as Record<string, string>
})

const fieldError = ref<Record<string, string>>({})

const dialogTitle = computed(() =>
  mode.value === 'create' ? t('ai.engines.create') : t('ai.engines.edit')
)

const secretFields = computed(() => SECRET_FIELDS[form.kind] ?? [])

function kindLabel(kind: string): string {
  const key = `ai.engines.kind${kind.charAt(0).toUpperCase()}${kind.slice(1)}`
  const label = t(key)
  return label === key ? kind : label
}

function validateName(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const name = String(value ?? '').trim()
  if (!name) {
    callback(new Error(t('ai.engines.validation.nameRequired')))
    return
  }
  if (!NAME_PATTERN.test(name)) {
    callback(new Error(t('ai.engines.validation.namePattern')))
    return
  }
  callback()
}

function validatePriority(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const priority = Number(value)
  if (!Number.isInteger(priority) || priority < PRIORITY_MIN || priority > PRIORITY_MAX) {
    callback(new Error(t('ai.engines.validation.priorityRange')))
    return
  }
  callback()
}

const formRules: FormRules = {
  name: [{ validator: validateName, trigger: 'blur' }],
  priority: [{ validator: validatePriority, trigger: 'blur' }]
}

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  try {
    rows.value = await listOcrEngines()
    loadFailed.value = false
  } catch {
    rows.value = []
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.kind = 'rapidocr'
  form.priority = 100
  form.enabled = true
  form.config = ''
  form.secret = {}
  fieldError.value = {}
}

function openCreate() {
  mode.value = 'create'
  editingId.value = ''
  resetForm()
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row: AiOcrEngineRecord) {
  mode.value = 'edit'
  editingId.value = row.oid
  resetForm()
  form.name = row.name
  form.kind = row.kind
  form.priority = row.priority
  form.enabled = row.enabled
  form.config = Object.keys(row.config ?? {}).length
    ? JSON.stringify(row.config, null, 2)
    : ''
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function parseConfig(text: string): { ok: boolean; value?: Record<string, unknown> } {
  const trimmed = text.trim()
  if (!trimmed) {
    return { ok: true }
  }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false }
    }
    return { ok: true, value: parsed as Record<string, unknown> }
  } catch {
    return { ok: false }
  }
}

function collectSecret(): Record<string, string> | undefined {
  const secret: Record<string, string> = {}
  for (const field of secretFields.value) {
    const value = (form.secret[field] ?? '').trim()
    if (value) {
      secret[field] = value
    }
  }
  if (Object.keys(secret).length === 0) {
    return undefined
  }
  return secret
}

function applyServerError(error: unknown) {
  const code = aiErrorCodeOf(error)
  const copy = aiErrorCopy(code, t)
  if (code === 'AI_OCR_ENGINE_DUPLICATE') {
    fieldError.value = { name: `${copy.title}：${copy.hint}` }
    return
  }
  fieldError.value = { form: `${copy.title}：${copy.hint}` }
}

async function submit() {
  if (submitting.value || !formRef.value) {
    return
  }
  submitting.value = true
  fieldError.value = {}
  try {
    const valid = await formRef.value.validate().catch(() => false)
    if (!valid) {
      return
    }

    const name = form.name.trim()
    const config = parseConfig(form.config)
    if (!config.ok) {
      fieldError.value = { config: t('ai.engines.validation.configInvalid') }
      return
    }
    const secret = collectSecret()

    if (mode.value === 'create') {
      const missing = secretFields.value.filter((field) => !(form.secret[field] ?? '').trim())
      if (missing.length > 0) {
        fieldError.value = { secret: t('ai.engines.validation.secretRequired') }
        return
      }
      await createOcrEngine({
        name,
        kind: form.kind,
        priority: form.priority,
        enabled: form.enabled,
        ...(config.value ? { config: config.value } : {}),
        ...(secret ? { secret } : {})
      })
      dialogVisible.value = false
      ElMessage.success(t('ai.engines.createdNotice'))
    } else {
      const payload: {
        name?: string
        config?: Record<string, unknown>
        secret?: Record<string, string>
        enabled?: boolean
        priority?: number
      } = {}
      const original = rows.value.find((row) => row.oid === editingId.value)
      if (name !== original?.name) {
        payload.name = name
      }
      if (config.value) {
        payload.config = config.value
      }
      if (secret) {
        payload.secret = secret
      }
      if (form.enabled !== original?.enabled) {
        payload.enabled = form.enabled
      }
      if (form.priority !== original?.priority) {
        payload.priority = form.priority
      }
      if (Object.keys(payload).length === 0) {
        ElMessage.info(t('ai.keys.noChanges'))
        return
      }
      await updateOcrEngine(editingId.value, payload)
      dialogVisible.value = false
      ElMessage.success(t('ai.engines.savedNotice'))
    }
    await load()
  } catch (error) {
    applyServerError(error)
  } finally {
    submitting.value = false
  }
}

async function remove(row: AiOcrEngineRecord) {
  if (deletingId.value) {
    return
  }
  deletingId.value = row.oid
  try {
    await ElMessageBox.confirm(t('ai.engines.deleteConfirm'), t('ai.engines.deleteConfirmTitle'), {
      type: 'warning',
      confirmButtonText: t('ai.common.delete'),
      cancelButtonText: t('ai.common.cancel'),
      confirmButtonClass: 'el-button--danger',
      closeOnClickModal: false
    })
  } catch {
    deletingId.value = ''
    return
  }
  try {
    await deleteOcrEngine(row.oid)
    rows.value = rows.value.filter((item) => item.oid !== row.oid)
    ElMessage.success(t('ai.engines.deletedNotice'))
  } catch (error) {
    applyServerError(error)
  } finally {
    deletingId.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never" v-loading="loading">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ t('ai.engines.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--zv-label)">
              {{ t('ai.engines.subtitle') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton @click="load">
              <ElIcon class="mr-1"><Refresh /></ElIcon>
              {{ t('ai.common.refresh') }}
            </ElButton>
            <ElButton type="primary" @click="openCreate">
              <ElIcon class="mr-1"><Plus /></ElIcon>
              {{ t('ai.engines.create') }}
            </ElButton>
          </div>
        </div>
      </template>

      <ElTable :data="rows" row-key="oid" stripe>
        <ElTableColumn prop="name" :label="t('ai.engines.name')" min-width="140" show-overflow-tooltip />
        <ElTableColumn :label="t('ai.engines.kind')" width="120" align="center">
          <template #default="{ row }">
            <ElTag size="small" type="info">{{ kindLabel(row.kind) }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.engines.secret')" min-width="160">
          <template #default="{ row }">
            <div class="flex flex-col">
              <span class="tabular-nums text-xs">{{ row.key_hint ?? '—' }}</span>
              <span class="text-xs" style="color: var(--zv-label)">
                {{ row.has_secret ? t('ai.engines.hasSecret') : t('ai.engines.noSecretYet') }}
              </span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="priority" :label="t('ai.engines.priority')" width="90" align="right">
          <template #default="{ row }">
            <span class="tabular-nums">{{ row.priority }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.common.status')" width="90" align="center">
          <template #default="{ row }">
            <ElTag :type="row.enabled ? 'primary' : 'info'" size="small">
              {{ row.enabled ? t('ai.common.enabled') : t('ai.common.disabled') }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="updated_at" :label="t('ai.common.updatedAt')" min-width="150">
          <template #default="{ row }">
            <span class="tabular-nums text-xs">{{ row.updated_at ?? '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.common.actions')" width="150" fixed="right" align="right">
          <template #default="{ row }">
            <div class="flex items-center justify-end gap-1">
              <ElButton text type="primary" size="small" @click="openEdit(row)">
                <ElIcon class="mr-1"><Edit /></ElIcon>
                {{ t('ai.common.edit') }}
              </ElButton>
              <ElButton
                text
                type="danger"
                size="small"
                :loading="deletingId === row.oid"
                :disabled="Boolean(deletingId) && deletingId !== row.oid"
                @click="remove(row)"
              >
                <ElIcon class="mr-1"><Delete /></ElIcon>
                {{ t('ai.common.delete') }}
              </ElButton>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="loadFailed ? t('ai.engines.loadFailed') : t('ai.engines.empty')"
            :image-size="90"
          />
        </template>
      </ElTable>
    </ElCard>

    <ElDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="min(680px, 94vw)"
      top="6vh"
      :close-on-click-modal="false"
      :close-on-press-escape="!submitting"
      :show-close="!submitting"
      append-to-body
    >
      <ElForm
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        class="ai-engine-form"
      >
        <div class="grid gap-x-4 sm:grid-cols-2">
          <ElFormItem :label="t('ai.engines.name')" prop="name">
            <ElInput v-model="form.name" />
            <p v-if="fieldError.name" class="text-xs mt-1" style="color: var(--el-color-danger)">
              {{ fieldError.name }}
            </p>
          </ElFormItem>
          <ElFormItem :label="t('ai.engines.kind')" prop="kind">
            <ElSelect v-model="form.kind" class="w-full" :disabled="mode === 'edit'">
              <ElOption
                v-for="kind in KIND_OPTIONS"
                :key="kind"
                :label="kindLabel(kind)"
                :value="kind"
              />
            </ElSelect>
            <p v-if="mode === 'edit'" class="text-xs mt-1" style="color: var(--zv-label)">
              {{ t('ai.engines.kindLocked') }}
            </p>
          </ElFormItem>
        </div>

        <div class="grid gap-x-4 sm:grid-cols-2">
          <ElFormItem :label="t('ai.engines.priority')" prop="priority">
            <ElInputNumber v-model="form.priority" :min="PRIORITY_MIN" :max="PRIORITY_MAX" />
            <p class="text-xs mt-1" style="color: var(--zv-label)">{{ t('ai.engines.priorityHint') }}</p>
          </ElFormItem>
          <ElFormItem :label="t('ai.common.status')">
            <ElSwitch v-model="form.enabled" />
          </ElFormItem>
        </div>

        <ElFormItem :label="t('ai.engines.secret')" prop="secret">
          <div class="w-full flex flex-col gap-2">
            <template v-if="secretFields.length > 0">
              <div
                v-for="field in secretFields"
                :key="field"
                class="flex flex-wrap items-center gap-2"
              >
                <span class="z-label w-40">{{ t(SECRET_LABELS[field] ?? field) }}</span>
                <ElInput
                  v-model="form.secret[field]"
                  class="w-64"
                  type="password"
                  show-password
                />
              </div>
              <p class="text-xs" style="color: var(--zv-label)">{{ t('ai.engines.secretHint') }}</p>
            </template>
            <p v-else class="text-xs" style="color: var(--zv-label)">{{ t('ai.engines.noSecret') }}</p>
            <p v-if="fieldError.secret" class="text-xs" style="color: var(--el-color-danger)">
              {{ fieldError.secret }}
            </p>
          </div>
        </ElFormItem>

        <ElFormItem :label="t('ai.engines.config')" prop="config">
          <ElInput
            v-model="form.config"
            type="textarea"
            :rows="3"
            resize="none"
            :placeholder="t('ai.engines.configPlaceholder')"
          />
          <p v-if="fieldError.config" class="text-xs mt-1" style="color: var(--el-color-danger)">
            {{ fieldError.config }}
          </p>
        </ElFormItem>

        <p v-if="fieldError.form" class="text-xs" style="color: var(--el-color-danger)">
          {{ fieldError.form }}
        </p>
      </ElForm>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="submitting" @click="dialogVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="submitting" @click="submit">
            {{ mode === 'create' ? t('ai.common.create') : t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.ai-engine-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}
</style>

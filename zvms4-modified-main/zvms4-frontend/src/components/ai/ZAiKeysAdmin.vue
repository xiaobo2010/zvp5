<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElDatePicker,
  ElDialog,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInput,
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
import { AiRequestError, aiErrorCodeOf, aiErrorCopy, createKey, deleteKey, listKeys, updateKey } from '@/api/ai'
import type { AiKeyRecord, AiKeyScope, AiKeyUpdateInput } from '@/../types/ai'

const { t } = useI18n()

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/
const API_KEY_MIN = 8
const API_KEY_MAX = 512
const MODEL_MAX_LEN = 80
const MODELS_MAX = 20

const rows = ref<AiKeyRecord[]>([])
const loading = ref(false)
const loadFailed = ref(false)
const togglingId = ref('')
const deletingId = ref('')

type DialogMode = 'create' | 'edit'
const mode = ref<DialogMode>('create')
const dialogVisible = ref(false)
const editingId = ref('')
const submitting = ref(false)
const formRef = ref<FormInstance>()

interface HeaderRow {
  name: string
  value: string
}

const form = reactive({
  scope: 'shared' as AiKeyScope,
  name: '',
  baseUrl: '',
  models: '',
  apiKey: '',
  apiPath: '',
  enabled: true,
  expiresAt: undefined as Date | undefined,
  headers: [] as HeaderRow[]
})

const headersTouched = ref(false)
const fieldError = ref<Record<string, string>>({})

const dialogTitle = computed(() =>
  mode.value === 'create' ? t('ai.keys.create') : t('ai.keys.edit')
)

function parseModels(text: string): string[] {
  const seen: string[] = []
  for (const part of text.split(/[,，]/)) {
    const model = part.trim()
    if (model && !seen.includes(model)) {
      seen.push(model)
    }
  }
  return seen
}

function validateName(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const name = String(value ?? '').trim()
  if (!name) {
    callback(new Error(t('ai.keys.validation.nameRequired')))
    return
  }
  if (!NAME_PATTERN.test(name)) {
    callback(new Error(t('ai.keys.validation.namePattern')))
    return
  }
  callback()
}

function validateBaseUrl(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const url = String(value ?? '').trim()
  if (!url) {
    callback(new Error(t('ai.keys.validation.baseUrlRequired')))
    return
  }
  if (!/^https?:\/\//i.test(url)) {
    callback(new Error(t('ai.keys.validation.baseUrlPattern')))
    return
  }
  callback()
}

function validateApiKey(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const key = String(value ?? '').trim()
  if (!key) {
    if (mode.value === 'create') {
      callback(new Error(t('ai.keys.validation.apiKeyRequired')))
      return
    }
    callback()
    return
  }
  if (key.length < API_KEY_MIN || key.length > API_KEY_MAX) {
    callback(new Error(t('ai.keys.validation.apiKeyLength')))
    return
  }
  callback()
}

function validateModels(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const models = parseModels(String(value ?? ''))
  if (models.length > MODELS_MAX) {
    callback(new Error(t('ai.keys.validation.modelsTooMany')))
    return
  }
  if (models.some((model) => model.length > MODEL_MAX_LEN)) {
    callback(new Error(t('ai.keys.validation.modelTooLong')))
    return
  }
  callback()
}

const formRules: FormRules = {
  name: [{ validator: validateName, trigger: 'blur' }],
  baseUrl: [{ validator: validateBaseUrl, trigger: 'blur' }],
  apiKey: [{ validator: validateApiKey, trigger: 'blur' }],
  models: [{ validator: validateModels, trigger: 'blur' }]
}

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  try {
    rows.value = await listKeys()
    loadFailed.value = false
  } catch {
    rows.value = []
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

function canMutate(row: AiKeyRecord): boolean {
  return row.scope === 'shared' || row.owned
}

function resetForm() {
  form.scope = 'shared'
  form.name = ''
  form.baseUrl = ''
  form.models = ''
  form.apiKey = ''
  form.apiPath = ''
  form.enabled = true
  form.expiresAt = undefined
  form.headers = []
  headersTouched.value = false
  fieldError.value = {}
}

function openCreate() {
  mode.value = 'create'
  editingId.value = ''
  resetForm()
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row: AiKeyRecord) {
  mode.value = 'edit'
  editingId.value = row.oid
  resetForm()
  form.name = row.name
  form.baseUrl = row.base_url
  form.models = row.models.join(', ')
  form.apiPath = row.api_path ?? ''
  form.enabled = row.enabled
  form.expiresAt = row.expires_at ? new Date(row.expires_at) : undefined
  // 请求头的值不回显，只列出头名：留空即保持不变，填写即轮换。
  form.headers = Object.keys(row.headers ?? {}).map((name) => ({ name, value: '' }))
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function addHeader() {
  form.headers.push({ name: '', value: '' })
  headersTouched.value = true
}

function removeHeader(index: number) {
  form.headers.splice(index, 1)
  headersTouched.value = true
}

function collectHeaders(): Record<string, string> | undefined {
  if (!headersTouched.value) {
    return undefined
  }
  const headers: Record<string, string> = {}
  for (const row of form.headers) {
    const name = row.name.trim()
    const value = row.value.trim()
    if (!name || !value) {
      continue
    }
    headers[name] = value
  }
  return headers
}

function applyServerError(error: unknown) {
  const code = aiErrorCodeOf(error)
  const copy = aiErrorCopy(code, t)
  const detail = error instanceof AiRequestError ? error.message : ''
  if (code === 'AI_KEY_DUPLICATE') {
    fieldError.value = { name: `${copy.title}：${copy.hint}` }
    return
  }
  if (code === 'AI_KEY_BAD_EXPIRY') {
    fieldError.value = { expiresAt: `${copy.title}：${copy.hint}` }
    return
  }
  fieldError.value = {
    form: detail ? `${copy.title}：${copy.hint}（${detail}）` : `${copy.title}：${copy.hint}`
  }
}

/** 到期时间折成后端要的 ISO 字符串；空值表示永不过期。 */
function expiryPayload(): string | null {
  return form.expiresAt ? form.expiresAt.toISOString() : null
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
    const baseUrl = form.baseUrl.trim()
    const models = parseModels(form.models)
    const apiKey = form.apiKey.trim()
    const headers = collectHeaders()

    if (form.expiresAt && form.expiresAt.getTime() <= Date.now()) {
      fieldError.value = { expiresAt: t('ai.keys.validation.expiresInPast') }
      return
    }

    if (mode.value === 'create') {
      await createKey({
        scope: form.scope,
        name,
        base_url: baseUrl,
        models,
        api_key: apiKey,
        enabled: form.enabled,
        ...(headers ? { headers } : {}),
        ...(form.apiPath.trim() ? { api_path: form.apiPath.trim() } : {}),
        ...(form.expiresAt ? { expires_at: expiryPayload() } : {})
      })
      dialogVisible.value = false
      ElMessage.success(t('ai.keys.createdNotice'))
    } else {
      const original = rows.value.find((row) => row.oid === editingId.value)
      if (!original) {
        dialogVisible.value = false
        return
      }
      const payload: AiKeyUpdateInput = {}
      if (name !== original.name) {
        payload.name = name
      }
      if (baseUrl !== original.base_url) {
        payload.base_url = baseUrl
      }
      if (models.join(',') !== original.models.join(',')) {
        payload.models = models
      }
      if (apiKey) {
        payload.api_key = apiKey
      }
      if (form.enabled !== original.enabled) {
        payload.enabled = form.enabled
      }
      if (headers) {
        payload.headers = headers
      }
      const apiPath = form.apiPath.trim()
      if (apiPath !== (original.api_path ?? '')) {
        payload.api_path = apiPath
      }
      // 到期时间不做差异比较：编辑提交时永远带上（值或 null，null = 清除到期）。
      payload.expires_at = expiryPayload()
      await updateKey(editingId.value, payload)
      dialogVisible.value = false
      ElMessage.success(t('ai.keys.savedNotice'))
    }
    await load()
  } catch (error) {
    applyServerError(error)
  } finally {
    submitting.value = false
  }
}

async function toggleEnabled(row: AiKeyRecord) {
  if (togglingId.value || !canMutate(row)) {
    return
  }
  togglingId.value = row.oid
  try {
    const updated = await updateKey(row.oid, { enabled: !row.enabled })
    const index = rows.value.findIndex((item) => item.oid === row.oid)
    if (index >= 0) {
      rows.value[index] = updated
    }
  } catch (error) {
    applyServerError(error)
  } finally {
    togglingId.value = ''
  }
}

async function remove(row: AiKeyRecord) {
  if (deletingId.value || !canMutate(row)) {
    return
  }
  deletingId.value = row.oid
  try {
    await ElMessageBox.confirm(t('ai.keys.deleteConfirm'), t('ai.keys.deleteConfirmTitle'), {
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
    await deleteKey(row.oid)
    rows.value = rows.value.filter((item) => item.oid !== row.oid)
    ElMessage.success(t('ai.keys.deletedNotice'))
  } catch (error) {
    applyServerError(error)
  } finally {
    deletingId.value = ''
  }
}

function modelPreview(row: AiKeyRecord): string {
  return row.models.join(', ')
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

/** 已过期的凭据照常列出（后端不隐藏行），只做醒目标记。 */
function isExpired(row: AiKeyRecord): boolean {
  if (!row.expires_at) {
    return false
  }
  const time = new Date(row.expires_at).getTime()
  return !Number.isNaN(time) && time <= Date.now()
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
            <span>{{ t('ai.keys.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--zv-label)">
              {{ t('ai.keys.subtitle') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton @click="load">
              <ElIcon class="mr-1"><Refresh /></ElIcon>
              {{ t('ai.common.refresh') }}
            </ElButton>
            <ElButton type="primary" @click="openCreate">
              <ElIcon class="mr-1"><Plus /></ElIcon>
              {{ t('ai.keys.create') }}
            </ElButton>
          </div>
        </div>
      </template>

      <ElTable :data="rows" row-key="oid" stripe>
        <ElTableColumn :label="t('ai.keys.scope')" width="90" align="center">
          <template #default="{ row }">
            <ElTag :type="row.scope === 'shared' ? 'primary' : 'info'" size="small">
              {{
                row.scope === 'shared'
                  ? t('ai.keys.scopeShared')
                  : t('ai.keys.scopePersonal')
              }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="name" :label="t('ai.keys.name')" min-width="140" show-overflow-tooltip />
        <ElTableColumn :label="t('ai.keys.keyHint')" width="120">
          <template #default="{ row }">
            <span class="tabular-nums">{{ row.key_hint ?? '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="base_url"
          :label="t('ai.keys.baseUrl')"
          min-width="200"
          show-overflow-tooltip
        />
        <ElTableColumn :label="t('ai.keys.models')" min-width="180">
          <template #default="{ row }">
            <span class="text-xs">{{ modelPreview(row) || '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.common.status')" width="90" align="center">
          <template #default="{ row }">
            <ElSwitch
              :model-value="row.enabled"
              :loading="togglingId === row.oid"
              :disabled="!canMutate(row) || Boolean(togglingId)"
              @change="toggleEnabled(row)"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.keys.expires')" width="170">
          <template #default="{ row }">
            <div v-if="!row.expires_at" class="flex flex-col">
              <span class="text-xs" style="color: var(--zv-label)">
                {{ t('ai.keys.neverExpires') }}
              </span>
            </div>
            <div v-else class="flex flex-col gap-1">
              <ElTag
                v-if="isExpired(row)"
                type="danger"
                size="small"
                class="ai-key-expires__badge"
                data-expired="true"
              >
                {{ t('ai.keys.expired') }}
              </ElTag>
              <span class="tabular-nums text-xs">{{ formatTime(row.expires_at) }}</span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.keys.owned')" width="90" align="center">
          <template #default="{ row }">
            <ElTag v-if="row.owned" type="success" size="small">
              {{ t('ai.keys.owned') }}
            </ElTag>
            <span v-else style="color: var(--zv-label)">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="updated_at"
          :label="t('ai.common.updatedAt')"
          min-width="150"
        >
          <template #default="{ row }">
            <span class="tabular-nums text-xs">{{ row.updated_at ?? '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn :label="t('ai.common.actions')" width="150" fixed="right" align="right">
          <template #default="{ row }">
            <div v-if="canMutate(row)" class="flex items-center justify-end gap-1">
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
            <span v-else class="text-xs" style="color: var(--zv-label)">
              {{ t('ai.keys.readOnlyHint') }}
            </span>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="loadFailed ? t('ai.keys.loadFailed') : t('ai.keys.empty')"
            :image-size="90"
          />
        </template>
      </ElTable>
    </ElCard>

    <ElDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="min(760px, 94vw)"
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
        class="ai-keys-form"
      >
        <div class="grid gap-x-4 sm:grid-cols-2">
          <ElFormItem
            v-if="mode === 'create'"
            :label="t('ai.keys.scope')"
            prop="scope"
          >
            <ElSelect v-model="form.scope" class="w-full">
              <ElOption :label="t('ai.keys.scopeShared')" value="shared" />
              <ElOption :label="t('ai.keys.scopePersonal')" value="personal" />
            </ElSelect>
          </ElFormItem>
          <ElFormItem :label="t('ai.keys.name')" prop="name">
            <ElInput v-model="form.name" :placeholder="t('ai.keys.namePlaceholder')" />
            <p v-if="fieldError.name" class="text-xs mt-1" style="color: var(--el-color-danger)">
              {{ fieldError.name }}
            </p>
          </ElFormItem>
        </div>

        <ElFormItem :label="t('ai.keys.baseUrl')" prop="baseUrl">
          <ElInput v-model="form.baseUrl" :placeholder="t('ai.keys.baseUrlPlaceholder')" />
        </ElFormItem>

        <ElFormItem :label="t('ai.keys.models')" prop="models">
          <ElInput v-model="form.models" :placeholder="t('ai.keys.modelsPlaceholder')" />
        </ElFormItem>

        <ElFormItem :label="t('ai.keys.apiKey')" prop="apiKey">
          <ElInput
            v-model="form.apiKey"
            type="password"
            show-password
            :placeholder="t('ai.keys.apiKeyPlaceholder')"
          />
          <p class="text-xs mt-1" style="color: var(--zv-label)">{{ t('ai.keys.apiKeyHint') }}</p>
        </ElFormItem>

        <ElFormItem :label="t('ai.keys.apiPath')" prop="apiPath">
          <ElInput v-model="form.apiPath" :placeholder="t('ai.keys.apiPathPlaceholder')" />
        </ElFormItem>

        <ElFormItem :label="t('ai.keys.expiresLabel')" prop="expiresAt">
          <ElDatePicker
            v-model="form.expiresAt"
            type="datetime"
            class="w-full"
            :placeholder="t('ai.keys.expiresPlaceholder')"
          />
          <p v-if="fieldError.expiresAt" class="text-xs mt-1" style="color: var(--el-color-danger)">
            {{ fieldError.expiresAt }}
          </p>
          <p class="text-xs mt-1" style="color: var(--zv-label)">{{ t('ai.keys.expiresHint') }}</p>
        </ElFormItem>

        <ElFormItem :label="t('ai.keys.headers')">
          <div class="w-full flex flex-col gap-2">
            <div
              v-for="(header, index) in form.headers"
              :key="index"
              class="flex flex-wrap items-center gap-2"
            >
              <ElInput
                v-model="header.name"
                class="w-52"
                :placeholder="t('ai.keys.headerName')"
              />
              <ElInput
                v-model="header.value"
                class="w-64"
                type="password"
                show-password
                :placeholder="t('ai.keys.headerValue')"
              />
              <ElButton text type="danger" size="small" @click="removeHeader(index)">
                {{ t('ai.keys.removeHeader') }}
              </ElButton>
            </div>
            <div class="flex items-center gap-2">
              <ElButton size="small" @click="addHeader">
                <ElIcon class="mr-1"><Plus /></ElIcon>
                {{ t('ai.keys.addHeader') }}
              </ElButton>
              <span class="text-xs" style="color: var(--zv-label)">
                {{ t('ai.keys.headersHint') }}
              </span>
            </div>
          </div>
        </ElFormItem>

        <ElFormItem :label="t('ai.common.status')">
          <ElSwitch v-model="form.enabled" />
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
.ai-keys-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}
</style>

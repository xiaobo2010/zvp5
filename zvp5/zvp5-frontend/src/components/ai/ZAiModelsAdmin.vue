<script setup lang="ts">
/**
 * AI 模型管理。
 *
 * 一张表同时承载两类行：
 * - in_catalog 为 true：来自渠道目录，编辑写一条覆盖，删除覆盖后回落目录默认值；
 * - in_catalog 为 false：手工登记的行，只能编辑，不能删除。
 * 因此「删除」按钮只对目录行开放，并统一走二次确认。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElInputNumber } from 'element-plus/es/components/input-number/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElSwitch } from 'element-plus/es/components/switch/index.mjs'
import { ElTable } from 'element-plus/es/components/table/index.mjs'
import { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { Delete, Download, Edit, Refresh, Search } from '@element-plus/icons-vue'
import { deleteAiModel, discoverAiModels, fetchAiModels, updateAiModel } from '@/api/ai/models'
import { isReauthCancelled, withReauth } from '@/plugins/reauth'
import { resolveErrorMessage } from '@/utils/errorMessage'
import type { AiModelDiscoverResult, AiModelPatch, AiModelRow } from '@/../types/ai'

interface EditForm {
  display_name: string
  context_length: number | null
  rate: number | null
  enabled: boolean
  notes: string
}

const { t } = useI18n()

const rows = ref<AiModelRow[]>([])
const loading = ref(false)
const loadError = ref('')
const keyword = ref('')
const deletingModel = ref('')

const dialogVisible = ref(false)
const editingModel = ref('')
const submitting = ref(false)
const fieldError = ref<Record<string, string>>({})

const form = reactive<EditForm>({
  display_name: '',
  context_length: null,
  rate: null,
  enabled: true,
  notes: ''
})

/** 渠道列表：后端可能只给 provider，也可能给 providers；页面统一成数组。 */
function providersOf(row: AiModelRow): string[] {
  if (row.providers.length > 0) {
    return row.providers
  }
  return row.provider ? [row.provider] : []
}

function displayOf(value: string | null): string {
  return value && value.trim() ? value : t('ai.common.none')
}

function numberText(value: number | null): string {
  return value === null ? t('ai.common.none') : String(value)
}

const filteredRows = computed<AiModelRow[]>(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) {
    return rows.value
  }
  return rows.value.filter((row) => {
    const haystack = [row.model, row.display_name ?? '', ...providersOf(row)]
    return haystack.some((item) => item.toLowerCase().includes(needle))
  })
})

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    rows.value = await fetchAiModels()
  } catch (error) {
    rows.value = []
    loadError.value = resolveErrorMessage(error, 'ai.models.loadFailed')
  } finally {
    loading.value = false
  }
}

/* ------------------------------------------------------------------ 从上游获取 */

const discoverVisible = ref(false)
const discoverProvider = ref('')
const discoverLoading = ref(false)
const discoverError = ref('')
const discoverResult = ref<AiModelDiscoverResult | null>(null)
const discoverApplying = ref(false)

/** 渠道选项：当前行数据里 providers 的并集；老数据只有 provider，providersOf 已兜底。 */
const providerOptions = computed<string[]>(() => {
  const seen = new Set<string>()
  for (const row of rows.value) {
    for (const provider of providersOf(row)) {
      const name = provider.trim()
      if (name) {
        seen.add(name)
      }
    }
  }
  return [...seen]
})

const discoverBusy = computed(() => discoverLoading.value || discoverApplying.value)
const discoverHasRows = computed(() => Boolean(discoverResult.value?.models.length))

function contextText(value: number | null): string {
  return value === null ? t('ai.models.discover.contextMissing') : String(value)
}

function openDiscover() {
  discoverError.value = ''
  discoverResult.value = null
  discoverProvider.value = providerOptions.value[0] ?? ''
  discoverVisible.value = true
}

/** 预览：只读上游，不改库；密码再验证与凭据写路径共用同一张短令牌。 */
async function loadPreview() {
  if (discoverBusy.value) {
    return
  }
  const provider = discoverProvider.value.trim()
  if (!provider) {
    discoverResult.value = null
    discoverError.value = t('ai.models.discover.providerRequired')
    return
  }
  discoverLoading.value = true
  discoverError.value = ''
  try {
    discoverResult.value = await withReauth(t('ai.models.discover.reauthReason'), (token) =>
      discoverAiModels(provider, false, token)
    )
  } catch (error) {
    if (isReauthCancelled(error)) {
      return
    }
    discoverResult.value = null
    discoverError.value = resolveErrorMessage(error, 'ai.models.discover.failed')
  } finally {
    discoverLoading.value = false
  }
}

/** 回写：把上游给出的上下文长度合并进渠道凭据，然后刷新列表看生效结果。 */
async function applyContexts() {
  if (discoverBusy.value || !discoverResult.value) {
    return
  }
  const provider = discoverProvider.value.trim() || discoverResult.value.provider
  discoverApplying.value = true
  discoverError.value = ''
  try {
    const result = await withReauth(t('ai.models.discover.reauthReason'), (token) =>
      discoverAiModels(provider, true, token)
    )
    discoverResult.value = result
    ElMessage.success(t('ai.models.discover.applyOk', { count: result.applied }))
    await load()
  } catch (error) {
    if (isReauthCancelled(error)) {
      return
    }
    ElMessage.error(resolveErrorMessage(error, 'ai.models.discover.applyFailed'))
  } finally {
    discoverApplying.value = false
  }
}

function openEdit(row: AiModelRow) {
  editingModel.value = row.model
  form.display_name = row.display_name ?? ''
  form.context_length = row.context_length
  form.rate = row.rate
  form.enabled = row.enabled
  form.notes = row.notes ?? ''
  fieldError.value = {}
  dialogVisible.value = true
}

/** 只把改动过的字段放进 PUT，避免把目录默认值「写死」成覆盖。 */
function buildPatch(row: AiModelRow): AiModelPatch {
  const patch: AiModelPatch = {}
  const displayName = form.display_name.trim()
  if (displayName !== (row.display_name ?? '')) {
    patch.display_name = displayName || null
  }
  const notes = form.notes.trim()
  if (notes !== (row.notes ?? '')) {
    patch.notes = notes || null
  }
  if (form.context_length !== row.context_length) {
    patch.context_length = form.context_length
  }
  if (form.rate !== row.rate) {
    patch.rate = form.rate
  }
  if (form.enabled !== row.enabled) {
    patch.enabled = form.enabled
  }
  return patch
}

/** 上下文窗口：正整数或留空；倍率：大于 0 的小数。 */
function validateForm(): boolean {
  fieldError.value = {}
  const context = form.context_length
  if (context !== null && (!Number.isInteger(context) || context <= 0)) {
    fieldError.value.context = t('ai.models.invalidContext')
    return false
  }
  const rate = form.rate
  if (rate !== null && (!Number.isFinite(rate) || rate <= 0)) {
    fieldError.value.rate = t('ai.models.invalidRate')
    return false
  }
  return true
}

async function submit() {
  if (submitting.value) {
    return
  }
  const original = rows.value.find((row) => row.model === editingModel.value)
  if (!original) {
    return
  }
  if (!validateForm()) {
    return
  }
  const patch = buildPatch(original)
  if (Object.keys(patch).length === 0) {
    ElMessage.info(t('ai.models.noChanges'))
    return
  }
  submitting.value = true
  try {
    await updateAiModel(original.model, patch)
    dialogVisible.value = false
    ElMessage.success(t('ai.models.saveOk'))
    await load()
  } catch (error) {
    ElMessage.error(resolveErrorMessage(error, 'ai.models.saveFailed'))
  } finally {
    submitting.value = false
  }
}

async function remove(row: AiModelRow) {
  if (deletingModel.value || !row.in_catalog) {
    return
  }
  deletingModel.value = row.model
  try {
    await ElMessageBox.confirm(t('ai.models.confirmDelete'), t('ai.models.deleteTitle'), {
      type: 'warning',
      confirmButtonText: t('ai.common.delete'),
      cancelButtonText: t('ai.common.cancel'),
      confirmButtonClass: 'el-button--danger',
      closeOnClickModal: false
    })
  } catch {
    deletingModel.value = ''
    return
  }
  try {
    await deleteAiModel(row.model)
    rows.value = rows.value.filter((item) => item.model !== row.model)
    ElMessage.success(t('ai.models.deletedOk'))
  } catch (error) {
    ElMessage.error(resolveErrorMessage(error, 'ai.models.deleteFailed'))
  } finally {
    deletingModel.value = ''
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="yg-card" v-loading="loading">
    <header class="yg-card__header">
      <div class="flex flex-col gap-1 min-w-0">
        <h2 class="yg-card__title">{{ t('ai.models.title') }}</h2>
        <p class="yg-text-xs yg-text-tertiary">{{ t('ai.models.subtitle') }}</p>
      </div>
      <div class="yg-card__extra">
        <ElButton :disabled="discoverBusy" @click="openDiscover">
          <ElIcon class="mr-1"><Download /></ElIcon>
          {{ t('ai.models.discover.open') }}
        </ElButton>
        <ElButton :loading="loading" @click="load">
          <ElIcon class="mr-1"><Refresh /></ElIcon>
          {{ t('ai.common.refresh') }}
        </ElButton>
      </div>
    </header>

    <div class="yg-card__body">
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <ElInput
          v-model="keyword"
          class="w-64"
          clearable
          :placeholder="t('ai.models.searchPlaceholder')"
        >
          <template #prefix>
            <ElIcon><Search /></ElIcon>
          </template>
        </ElInput>
        <p class="yg-text-xs yg-text-tertiary">{{ t('ai.models.searchHint') }}</p>
      </div>

      <div v-if="loadError" class="py-6 flex flex-col items-center gap-3">
        <p class="yg-text-sm yg-text-danger">{{ loadError }}</p>
        <ElButton size="small" :loading="loading" @click="load">
          {{ t('ai.models.retry') }}
        </ElButton>
      </div>

      <p
        v-else-if="!loading && !filteredRows.length"
        class="py-8 text-center yg-text-sm yg-text-tertiary"
      >
        {{ t('ai.models.empty') }}
      </p>

      <div v-else class="ai-models__scroll">
        <ElTable :data="filteredRows" row-key="model" stripe>
          <ElTableColumn :label="t('ai.models.columns.model')" min-width="200">
            <template #default="{ row }">
              <span class="ai-models__ellipsis tabular-nums" :title="row.model">
                {{ row.model }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.providers')" min-width="180">
            <template #default="{ row }">
              <div v-if="providersOf(row).length" class="flex flex-wrap items-center gap-1">
                <ElTag
                  v-for="provider in providersOf(row)"
                  :key="provider"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ provider }}
                </ElTag>
              </div>
              <span v-else class="yg-text-tertiary">{{ t('ai.common.none') }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.displayName')" min-width="160">
            <template #default="{ row }">
              <span class="ai-models__ellipsis" :title="row.display_name ?? ''">
                {{ displayOf(row.display_name) }}
              </span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.contextLength')" width="120" align="right">
            <template #default="{ row }">
              <span class="tabular-nums">{{ numberText(row.context_length) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.rate')" width="100" align="right">
            <template #default="{ row }">
              <span class="tabular-nums">{{ numberText(row.rate) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.enabled')" width="90" align="center">
            <template #default="{ row }">
              <ElTag :type="row.enabled ? 'primary' : 'info'" size="small">
                {{ row.enabled ? t('ai.common.enabled') : t('ai.common.disabled') }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.models.columns.catalog')" width="110" align="center">
            <template #default="{ row }">
              <ElTag :type="row.in_catalog ? 'success' : 'info'" size="small" effect="plain">
                {{ row.in_catalog ? t('ai.models.inCatalog') : t('ai.models.manual') }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.common.actions')" width="170" fixed="right" align="right">
            <template #default="{ row }">
              <div class="flex items-center justify-end gap-1">
                <ElButton text type="primary" size="small" @click="openEdit(row)">
                  <ElIcon class="mr-1"><Edit /></ElIcon>
                  {{ t('ai.models.edit') }}
                </ElButton>
                <!-- 删除只对目录行开放：删掉的是覆盖，手工登记的行没有可回落的值 -->
                <ElButton
                  text
                  type="danger"
                  size="small"
                  :loading="deletingModel === row.model"
                  :disabled="!row.in_catalog || (Boolean(deletingModel) && deletingModel !== row.model)"
                  :title="row.in_catalog ? t('ai.models.delete') : t('ai.models.deleteHint')"
                  @click="remove(row)"
                >
                  <ElIcon class="mr-1"><Delete /></ElIcon>
                  {{ t('ai.models.delete') }}
                </ElButton>
              </div>
            </template>
          </ElTableColumn>
        </ElTable>
      </div>
    </div>

    <ElDialog
      v-model="dialogVisible"
      :title="t('ai.models.editTitle')"
      width="min(560px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!submitting"
      :show-close="!submitting"
      append-to-body
    >
      <ElForm label-position="top" class="ai-models-form">
        <ElFormItem :label="t('ai.models.columns.model')">
          <span class="yg-text-sm tabular-nums">{{ editingModel }}</span>
        </ElFormItem>

        <div class="grid gap-x-4 sm:grid-cols-2">
          <ElFormItem :label="t('ai.models.columns.displayName')">
            <ElInput v-model="form.display_name" :disabled="submitting" />
          </ElFormItem>
          <ElFormItem :label="t('ai.models.columns.enabled')">
            <ElSwitch v-model="form.enabled" :disabled="submitting" />
          </ElFormItem>
        </div>

        <div class="grid gap-x-4 sm:grid-cols-2">
          <ElFormItem :label="t('ai.models.columns.contextLength')">
            <ElInputNumber
              v-model="form.context_length"
              :min="1"
              :step="1024"
              :precision="0"
              class="w-full"
              :disabled="submitting"
            />
            <p class="yg-text-xs yg-text-tertiary mt-1">{{ t('ai.models.contextHint') }}</p>
            <p v-if="fieldError.context" class="yg-text-xs yg-text-danger mt-1">
              {{ fieldError.context }}
            </p>
          </ElFormItem>
          <ElFormItem :label="t('ai.models.columns.rate')">
            <ElInputNumber
              v-model="form.rate"
              :min="0.01"
              :step="0.25"
              :precision="2"
              class="w-full"
              :disabled="submitting"
            />
            <p class="yg-text-xs yg-text-tertiary mt-1">{{ t('ai.models.rateHint') }}</p>
            <p v-if="fieldError.rate" class="yg-text-xs yg-text-danger mt-1">
              {{ fieldError.rate }}
            </p>
          </ElFormItem>
        </div>

        <ElFormItem :label="t('ai.models.columns.notes')">
          <ElInput
            v-model="form.notes"
            type="textarea"
            :rows="3"
            resize="none"
            :placeholder="t('ai.models.notesPlaceholder')"
            :disabled="submitting"
          />
        </ElFormItem>
      </ElForm>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="submitting" @click="dialogVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="submitting" @click="submit">
            {{ t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>

    <ElDialog
      v-model="discoverVisible"
      :title="t('ai.models.discover.title')"
      width="min(720px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!discoverBusy"
      :show-close="!discoverBusy"
      append-to-body
    >
      <div class="flex flex-col gap-3">
        <p class="yg-text-xs yg-text-tertiary">{{ t('ai.models.discover.hint') }}</p>

        <div class="flex flex-wrap items-end gap-2">
          <div class="flex-1 min-w-0">
            <p class="yg-text-xs yg-text-tertiary mb-1">{{ t('ai.models.discover.provider') }}</p>
            <ElSelect
              v-if="providerOptions.length"
              v-model="discoverProvider"
              class="w-full"
              :placeholder="t('ai.models.discover.providerPlaceholder')"
              :disabled="discoverBusy"
            >
              <ElOption
                v-for="provider in providerOptions"
                :key="provider"
                :label="provider"
                :value="provider"
              />
            </ElSelect>
            <!-- 列表里还没有任何渠道：没有可选项，只能手填凭据里登记的渠道名 -->
            <template v-else>
              <ElInput
                v-model="discoverProvider"
                :placeholder="t('ai.models.discover.providerPlaceholder')"
                :disabled="discoverBusy"
              />
              <p class="yg-text-xs yg-text-tertiary mt-1">
                {{ t('ai.models.discover.providerManualHint') }}
              </p>
            </template>
          </div>
          <ElButton
            type="primary"
            :loading="discoverLoading"
            :disabled="discoverApplying"
            @click="loadPreview"
          >
            <ElIcon class="mr-1"><Search /></ElIcon>
            {{ t('ai.models.discover.fetch') }}
          </ElButton>
        </div>

        <div v-if="discoverError" class="py-6 flex flex-col items-center gap-3">
          <p class="yg-text-sm yg-text-danger">{{ discoverError }}</p>
          <ElButton
            size="small"
            :loading="discoverLoading"
            :disabled="discoverApplying"
            @click="loadPreview"
          >
            {{ t('ai.models.discover.retry') }}
          </ElButton>
        </div>

        <p
          v-else-if="discoverResult && !discoverHasRows"
          class="py-8 text-center yg-text-sm yg-text-tertiary"
        >
          {{ t('ai.models.discover.empty') }}
        </p>

        <div
          v-else-if="discoverResult"
          v-loading="discoverLoading"
          class="ai-models__discover-scroll"
        >
          <ElTable :data="discoverResult.models" row-key="model" stripe>
            <ElTableColumn :label="t('ai.models.discover.columns.model')" min-width="200">
              <template #default="{ row }">
                <span class="ai-models__ellipsis tabular-nums" :title="row.model">
                  {{ row.model }}
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('ai.models.discover.columns.contextLength')"
              width="150"
              align="right"
            >
              <template #default="{ row }">
                <span class="tabular-nums">{{ contextText(row.context_length) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn
              :label="t('ai.models.discover.columns.inCredential')"
              width="140"
              align="center"
            >
              <template #default="{ row }">
                <ElTag :type="row.in_credential ? 'success' : 'info'" size="small" effect="plain">
                  {{ row.in_credential ? t('ai.models.discover.yes') : t('ai.models.discover.no') }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn :label="t('ai.models.discover.columns.status')" width="110" align="center">
              <template #default="{ row }">
                <ElTag :type="row.applied ? 'primary' : 'info'" size="small">
                  {{
                    row.applied
                      ? t('ai.models.discover.statusApplied')
                      : t('ai.models.discover.statusPending')
                  }}
                </ElTag>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>

        <p v-else class="py-8 text-center yg-text-sm yg-text-tertiary">
          {{ t('ai.models.discover.idle') }}
        </p>
      </div>

      <template #footer>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <p v-if="discoverHasRows" class="yg-text-xs yg-text-tertiary mr-auto">
            {{ t('ai.models.discover.applyHint') }}
          </p>
          <ElButton :disabled="discoverApplying" @click="discoverVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton
            type="primary"
            :loading="discoverApplying"
            :disabled="!discoverHasRows || discoverLoading"
            @click="applyContexts"
          >
            {{ t('ai.models.discover.apply') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </section>
</template>

<style scoped>
/* 窄屏不挤压列：表格自身横向滚动，页头与操作列保持原位 */
.ai-models__scroll {
  overflow-x: auto;
}

.ai-models__scroll :deep(.el-table) {
  min-width: 960px;
}

.ai-models__discover-scroll {
  overflow: auto;
  max-height: 320px;
}

.ai-models__discover-scroll :deep(.el-table) {
  min-width: 600px;
}

.ai-models__ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-models-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}
</style>

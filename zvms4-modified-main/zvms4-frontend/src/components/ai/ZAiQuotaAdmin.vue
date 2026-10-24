<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElProgress,
  ElSelect
} from 'element-plus'
import { Refresh, Setting } from '@element-plus/icons-vue'
import ZSelectPerson from '@/components/form/ZSelectPerson.vue'
import {
  aiErrorCodeOf,
  aiErrorCopy,
  resetUserQuota,
  setUserLimit,
  userQuota
} from '@/api/ai'
import type { AiQuotaEntry, AiQuotaKind, AiQuotaSnapshot } from '@/../types/ai'

const { t } = useI18n()

const userOid = ref('')
const loading = ref(false)
const failed = ref(false)
const snapshot = ref<AiQuotaSnapshot | null>(null)

const limitVisible = ref(false)
const limitKind = ref<AiQuotaKind>('ocr')
const limitValue = ref(0)
const saving = ref(false)

const ocr = computed<AiQuotaEntry | null>(() => snapshot.value?.ocr ?? null)
const chat = computed<AiQuotaEntry | null>(() => snapshot.value?.chat ?? null)

function percent(entry: AiQuotaEntry | null): number {
  if (!entry || entry.limit <= 0) {
    return 0
  }
  return Math.min(100, Math.round((entry.used / entry.limit) * 100))
}

async function load() {
  if (!userOid.value) {
    snapshot.value = null
    return
  }
  loading.value = true
  failed.value = false
  try {
    snapshot.value = await userQuota(userOid.value)
  } catch {
    snapshot.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(userOid, () => {
  void load()
})

function openLimit(entry: AiQuotaEntry | null, kind: AiQuotaKind) {
  limitKind.value = kind
  limitValue.value = entry?.limit ?? 0
  limitVisible.value = true
}

async function submitLimit() {
  if (saving.value || !userOid.value) {
    return
  }
  saving.value = true
  try {
    await setUserLimit(userOid.value, limitKind.value, limitValue.value)
    limitVisible.value = false
    ElMessage.success(t('ai.quotaAdmin.savedNotice'))
    await load()
  } catch (error) {
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  } finally {
    saving.value = false
  }
}

async function resetUsage(kind: AiQuotaKind) {
  if (!userOid.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      t('ai.quotaAdmin.resetConfirm'),
      t('ai.quotaAdmin.resetConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('ai.common.confirm'),
        cancelButtonText: t('ai.common.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await resetUserQuota(userOid.value, kind)
    ElMessage.success(t('ai.quotaAdmin.resetNotice'))
    await load()
  } catch (error) {
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  }
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ t('ai.quotaAdmin.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--zv-label)">
              {{ t('ai.quotaAdmin.subtitle') }}
            </span>
          </div>
          <ElButton :disabled="!userOid" @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </template>

      <div class="flex flex-wrap items-center gap-3">
        <span class="z-label">{{ t('ai.quotaAdmin.pickUser') }}</span>
        <div class="w-72">
          <ZSelectPerson v-model="userOid" :filter-start="6" full-width />
        </div>
      </div>
    </ElCard>

    <ElCard shadow="never" v-loading="loading">
      <template #header>
        <span class="z-label">{{ t('ai.quotaAdmin.title') }}</span>
      </template>

      <div v-if="!userOid" class="py-8 text-center text-sm" style="color: var(--zv-label)">
        {{ t('ai.quotaAdmin.noUser') }}
      </div>
      <p v-else-if="failed" class="py-8 text-center text-sm" style="color: var(--el-color-danger)">
        {{ t('ai.quotaAdmin.loadFailed') }}
      </p>
      <div v-else class="grid gap-4 sm:grid-cols-2">
        <div v-for="item in [
          { kind: 'ocr' as AiQuotaKind, title: t('ai.quotaAdmin.ocrTitle'), entry: ocr },
          { kind: 'chat' as AiQuotaKind, title: t('ai.quotaAdmin.chatTitle'), entry: chat }
        ]" :key="item.kind" class="z-tile rounded-lg p-4 flex flex-col gap-2">
          <div class="flex items-baseline justify-between gap-2">
            <span class="z-label">{{ item.title }}</span>
            <span class="text-xs tabular-nums" style="color: var(--zv-label)">
              {{ t('ai.common.period') }}：{{ item.entry?.period ?? '—' }}
            </span>
          </div>
          <p class="z-stat__value tabular-nums">
            {{ item.entry?.used ?? 0 }}
            <span class="text-sm font-normal" style="color: var(--zv-label)">
              / {{ item.entry?.limit ?? 0 }}
            </span>
          </p>
          <ElProgress :percentage="percent(item.entry)" :stroke-width="6" />
          <div class="flex flex-wrap items-center justify-end gap-2">
            <ElButton size="small" @click="resetUsage(item.kind)">
              {{ t('ai.quotaAdmin.reset') }}
            </ElButton>
            <ElButton size="small" type="primary" plain @click="openLimit(item.entry, item.kind)">
              <ElIcon class="mr-1"><Setting /></ElIcon>
              {{ t('ai.quotaAdmin.setLimit') }}
            </ElButton>
          </div>
        </div>
      </div>
    </ElCard>

    <ElDialog
      v-model="limitVisible"
      :title="t('ai.quotaAdmin.setLimitTitle')"
      width="min(420px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
      :show-close="!saving"
      append-to-body
    >
      <ElForm label-position="top">
        <ElFormItem :label="t('ai.quotaAdmin.limitKind')">
          <ElSelect v-model="limitKind" class="w-full">
            <ElOption :label="t('ai.quotaAdmin.ocrTitle')" value="ocr" />
            <ElOption :label="t('ai.quotaAdmin.chatTitle')" value="chat" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem :label="t('ai.quotaAdmin.limitValue')">
          <ElInputNumber v-model="limitValue" :min="0" :step="10" class="w-full" />
          <p class="text-xs mt-1" style="color: var(--zv-label)">{{ t('ai.quotaAdmin.limitHint') }}</p>
        </ElFormItem>
      </ElForm>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <ElButton :disabled="saving" @click="limitVisible = false">
            {{ t('ai.common.cancel') }}
          </ElButton>
          <ElButton type="primary" :loading="saving" @click="submitLimit">
            {{ t('ai.common.save') }}
          </ElButton>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

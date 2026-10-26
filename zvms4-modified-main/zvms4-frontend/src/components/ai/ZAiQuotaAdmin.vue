<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElProgress,
  ElSelect,
  ElTag
} from 'element-plus'
import { Refresh, Setting } from '@element-plus/icons-vue'
import ZSelectPerson from '@/components/form/ZSelectPerson.vue'
import {
  aiErrorCodeOf,
  aiErrorCopy,
  aiQuotaSourceLabelKey,
  resetUserQuota,
  setUserLimit,
  userQuota,
  type AiQuotaEntryView,
  type AiQuotaSnapshotView
} from '@/api/ai'
import type { AiQuotaKind } from '@/../types/ai'

const { t } = useI18n()

const userOid = ref('')
const loading = ref(false)
const failed = ref(false)
const snapshot = ref<AiQuotaSnapshotView | null>(null)

const limitVisible = ref(false)
const limitKind = ref<AiQuotaKind>('ocr')
const limitValue = ref(0)
const saving = ref(false)

const ocr = computed<AiQuotaEntryView | null>(() => snapshot.value?.ocr ?? null)
const chat = computed<AiQuotaEntryView | null>(() => snapshot.value?.chat ?? null)

/** 来源标签文案；后端没给 source（老版本）或给了未知值时返回 null，不渲染标签。 */
function sourceLabel(entry: AiQuotaEntryView | null): string | null {
  const key = aiQuotaSourceLabelKey(entry?.source)
  return key ? t(key) : null
}

function percent(entry: AiQuotaEntryView | null): number {
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

function openLimit(entry: AiQuotaEntryView | null, kind: AiQuotaKind) {
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
    <section class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.quotaAdmin.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.subtitle') }}</p>
        </div>
        <div class="yg-card__extra">
          <ElButton :disabled="!userOid" @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div class="flex flex-wrap items-center gap-3">
          <span class="yg-text-xs yg-text-tertiary">{{ t('ai.quotaAdmin.pickUser') }}</span>
          <div class="w-72">
            <ZSelectPerson v-model="userOid" :filter-start="6" full-width />
          </div>
        </div>
      </div>
    </section>

    <section class="yg-card" v-loading="loading">
      <header class="yg-card__header">
        <h2 class="yg-statistic__title">{{ t('ai.quotaAdmin.title') }}</h2>
      </header>

      <div class="yg-card__body">
        <!-- 自然月口径放在列表上方说明一次：管理员最常误读「设完下月又变回去」 -->
        <p class="yg-text-xs yg-text-tertiary mb-3">{{ t('ai.quotaAdmin.monthlyNotice') }}</p>
        <div v-if="!userOid" class="py-8 text-center yg-text-sm yg-text-tertiary">
          {{ t('ai.quotaAdmin.noUser') }}
        </div>
        <p v-else-if="failed" class="py-8 text-center yg-text-sm yg-text-danger">
          {{ t('ai.quotaAdmin.loadFailed') }}
        </p>
        <div v-else class="grid gap-4 sm:grid-cols-2">
          <div
            v-for="item in [
              { kind: 'ocr' as AiQuotaKind, title: t('ai.quotaAdmin.ocrTitle'), entry: ocr },
              { kind: 'chat' as AiQuotaKind, title: t('ai.quotaAdmin.chatTitle'), entry: chat }
            ]"
            :key="item.kind"
            class="yg-tile p-4 flex flex-col gap-2"
          >
            <div class="flex items-baseline justify-between gap-2">
              <span class="yg-statistic__title">{{ item.title }}</span>
              <span class="yg-text-xs yg-text-tertiary tabular-nums">
                {{ t('ai.common.period') }}：{{ item.entry?.period ?? '—' }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <!-- 次要色标签：来源只是解释数字怎么来的，不是需要警觉的状态 -->
              <ElTag v-if="sourceLabel(item.entry)" type="info" size="small" effect="plain">
                {{ sourceLabel(item.entry) }}
              </ElTag>
            </div>
            <p class="yg-statistic__value">
              {{ item.entry?.used ?? 0 }}
              <span class="yg-statistic__suffix">/ {{ item.entry?.limit ?? 0 }}</span>
            </p>
            <ElProgress :percentage="percent(item.entry)" :stroke-width="6" />
            <div class="flex flex-wrap items-center justify-end gap-2">
              <!-- 清空计数属于破坏性操作：红字按钮 + 二次确认（准则 5.1 / 5.2） -->
              <ElButton size="small" text type="danger" @click="resetUsage(item.kind)">
                {{ t('ai.quotaAdmin.reset') }}
              </ElButton>
              <ElButton size="small" type="primary" plain @click="openLimit(item.entry, item.kind)">
                <ElIcon class="mr-1"><Setting /></ElIcon>
                {{ t('ai.quotaAdmin.setLimit') }}
              </ElButton>
            </div>
          </div>
        </div>
      </div>
    </section>

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
          <p class="yg-text-xs yg-text-tertiary mt-1">{{ t('ai.quotaAdmin.limitHint') }}</p>
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

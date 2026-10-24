<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElCard, ElIcon, ElProgress } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { myQuota } from '@/api/ai'
import type { AiQuotaEntry } from '@/../types/ai'

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
const ocr = ref<AiQuotaEntry | null>(null)
const chat = ref<AiQuotaEntry | null>(null)

function percent(entry: AiQuotaEntry | null): number {
  if (!entry || entry.limit <= 0) {
    return 0
  }
  return Math.min(100, Math.round((entry.used / entry.limit) * 100))
}

const ocrPercent = computed(() => percent(ocr.value))
const chatPercent = computed(() => percent(chat.value))

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  failed.value = false
  try {
    const data = await myQuota()
    ocr.value = data.ocr
    chat.value = data.chat
  } catch {
    ocr.value = null
    chat.value = null
    failed.value = true
  } finally {
    loading.value = false
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
            <span>{{ t('ai.quota.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--zv-label)">
              {{ t('ai.quota.subtitle') }}
            </span>
          </div>
          <ElButton @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.quota.refresh') }}
          </ElButton>
        </div>
      </template>

      <div class="grid gap-4 sm:grid-cols-2">
        <div class="z-tile rounded-lg p-4">
          <div class="flex items-baseline justify-between gap-2">
            <span class="z-label">{{ t('ai.quota.ocrTitle') }}</span>
            <span class="text-xs" style="color: var(--zv-label)">{{ t('ai.quota.ocrUnit') }}</span>
          </div>
          <p class="z-stat__value tabular-nums mt-1">
            {{ ocr?.used ?? 0 }}
            <span class="text-sm font-normal" style="color: var(--zv-label)">
              / {{ ocr?.limit ?? 0 }}
            </span>
          </p>
          <ElProgress :percentage="ocrPercent" :stroke-width="6" class="mt-2" />
          <p class="text-xs mt-2 tabular-nums" style="color: var(--zv-label)">
            {{ t('ai.common.period') }}：{{ ocr?.period ?? '—' }}
          </p>
        </div>

        <div class="z-tile rounded-lg p-4">
          <div class="flex items-baseline justify-between gap-2">
            <span class="z-label">{{ t('ai.quota.chatTitle') }}</span>
            <span class="text-xs" style="color: var(--zv-label)">{{ t('ai.quota.chatUnit') }}</span>
          </div>
          <p class="z-stat__value tabular-nums mt-1">
            {{ chat?.used ?? 0 }}
            <span class="text-sm font-normal" style="color: var(--zv-label)">
              / {{ chat?.limit ?? 0 }}
            </span>
          </p>
          <ElProgress :percentage="chatPercent" :stroke-width="6" class="mt-2" />
          <p class="text-xs mt-2 tabular-nums" style="color: var(--zv-label)">
            {{ t('ai.common.period') }}：{{ chat?.period ?? '—' }}
          </p>
        </div>
      </div>

      <p v-if="failed" class="text-xs mt-3" style="color: var(--el-color-danger)">
        {{ t('ai.quota.loadFailed') }}
      </p>
    </ElCard>

    <ElCard shadow="never">
      <template #header>
        <span class="z-label">{{ t('ai.quota.ruleTitle') }}</span>
      </template>
      <ul class="text-sm flex flex-col gap-1" style="color: var(--zv-text-sub)">
        <li>{{ t('ai.quota.ruleOcr') }}</li>
        <li>{{ t('ai.quota.rulePersonal') }}</li>
        <li>{{ t('ai.quota.ruleShared') }}</li>
      </ul>
    </ElCard>
  </div>
</template>

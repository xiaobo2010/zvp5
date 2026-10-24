<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElIcon, ElProgress } from 'element-plus'
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
    <section class="yg-card" v-loading="loading">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.quota.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.subtitle') }}</p>
        </div>
        <div class="yg-card__extra">
          <ElButton @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="yg-tile p-4">
            <div class="flex items-baseline justify-between gap-2">
              <span class="yg-statistic__title">{{ t('ai.quota.ocrTitle') }}</span>
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.ocrUnit') }}</span>
            </div>
            <p class="yg-statistic__value">
              {{ ocr?.used ?? 0 }}
              <span class="yg-statistic__suffix">/ {{ ocr?.limit ?? 0 }}</span>
            </p>
            <ElProgress :percentage="ocrPercent" :stroke-width="6" class="mt-2" />
            <p class="yg-text-xs yg-text-tertiary mt-2 tabular-nums">
              {{ t('ai.common.period') }}：{{ ocr?.period ?? '—' }}
            </p>
          </div>

          <div class="yg-tile p-4">
            <div class="flex items-baseline justify-between gap-2">
              <span class="yg-statistic__title">{{ t('ai.quota.chatTitle') }}</span>
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.quota.chatUnit') }}</span>
            </div>
            <p class="yg-statistic__value">
              {{ chat?.used ?? 0 }}
              <span class="yg-statistic__suffix">/ {{ chat?.limit ?? 0 }}</span>
            </p>
            <ElProgress :percentage="chatPercent" :stroke-width="6" class="mt-2" />
            <p class="yg-text-xs yg-text-tertiary mt-2 tabular-nums">
              {{ t('ai.common.period') }}：{{ chat?.period ?? '—' }}
            </p>
          </div>
        </div>

        <p v-if="failed" class="yg-text-xs yg-text-danger mt-3">
          {{ t('ai.quota.loadFailed') }}
        </p>
      </div>
    </section>

    <section class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-statistic__title">{{ t('ai.quota.ruleTitle') }}</h2>
      </header>
      <div class="yg-card__body">
        <ul class="yg-text-sm yg-text-secondary flex flex-col gap-1">
          <li>{{ t('ai.quota.ruleOcr') }}</li>
          <li>{{ t('ai.quota.rulePersonal') }}</li>
          <li>{{ t('ai.quota.ruleShared') }}</li>
        </ul>
      </div>
    </section>
  </div>
</template>

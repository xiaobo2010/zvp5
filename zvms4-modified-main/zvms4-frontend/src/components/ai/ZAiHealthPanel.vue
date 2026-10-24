<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElEmpty,
  ElIcon,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getHealth } from '@/api/ai'
import type { AiHealthData } from '@/../types/ai'

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
const data = ref<AiHealthData | null>(null)

async function load() {
  if (loading.value) {
    return
  }
  loading.value = true
  failed.value = false
  try {
    data.value = await getHealth()
  } catch {
    data.value = null
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
            <span>{{ t('ai.health.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--zv-label)">
              {{ t('ai.health.subtitle') }}
            </span>
          </div>
          <ElButton @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </template>

      <div v-if="failed" class="py-6 text-center text-sm" style="color: var(--el-color-danger)">
        {{ t('ai.health.loadFailed') }}
      </div>

      <template v-else-if="data">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.enabled') }}</span>
            <ElTag :type="data.enabled ? 'primary' : 'info'" size="small" class="w-fit">
              {{ data.enabled ? t('ai.health.enabledOn') : t('ai.health.enabledOff') }}
            </ElTag>
          </div>
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.concurrency') }}</span>
            <span class="z-stat__value tabular-nums">{{ data.max_concurrency }}</span>
          </div>
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.localEngine') }}</span>
            <span class="text-sm">
              {{
                data.local_engine_ready
                  ? t('ai.health.localEngineReady')
                  : t('ai.health.localEngineCold')
              }}
            </span>
          </div>
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.quotaDefaults') }}</span>
            <span class="text-sm tabular-nums">
              {{ t('ai.health.defaultOcr') }}：{{ data.quota_defaults.ocr }}
            </span>
            <span class="text-sm tabular-nums">
              {{ t('ai.health.defaultChat') }}：{{ data.quota_defaults.chat }}
            </span>
          </div>
        </div>

        <div class="grid gap-3 sm:grid-cols-3 mt-3">
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.credentialPersonal') }}</span>
            <span class="z-stat__value tabular-nums">{{ data.credentials.personal }}</span>
          </div>
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.credentialShared') }}</span>
            <span class="z-stat__value tabular-nums">{{ data.credentials.shared }}</span>
          </div>
          <div class="z-tile rounded-lg p-3 flex flex-col gap-1">
            <span class="z-label">{{ t('ai.health.credentialServer') }}</span>
            <span class="z-stat__value tabular-nums">{{ data.credentials.server }}</span>
          </div>
        </div>
      </template>
    </ElCard>

    <template v-if="data">
      <ElCard shadow="never">
        <template #header>
          <span class="z-label">{{ t('ai.health.providers') }}</span>
        </template>
        <ElTable :data="data.providers ?? []" row-key="name" stripe>
          <ElTableColumn prop="name" :label="t('ai.health.columnName')" min-width="140" />
          <ElTableColumn prop="kind" :label="t('ai.health.columnKind')" width="120" />
          <ElTableColumn
            prop="base_url"
            :label="t('ai.health.columnBaseUrl')"
            min-width="180"
            show-overflow-tooltip
          />
          <ElTableColumn :label="t('ai.health.columnModels')" min-width="180">
            <template #default="{ row }">
              <span class="text-xs">{{ (row.models ?? []).join(', ') || '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.health.columnConfigured')" width="100" align="center">
            <template #default="{ row }">
              <ElTag :type="row.configured ? 'primary' : 'info'" size="small">
                {{ row.configured ? t('ai.common.yes') : t('ai.common.no') }}
              </ElTag>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :description="t('ai.health.modelsEmpty')" :image-size="80" />
          </template>
        </ElTable>
      </ElCard>

      <ElCard shadow="never">
        <template #header>
          <span class="z-label">{{ t('ai.health.sharedEntries') }}</span>
        </template>
        <ElTable :data="data.credentials?.shared_entries ?? []" row-key="name" stripe>
          <ElTableColumn prop="name" :label="t('ai.health.columnName')" min-width="140" />
          <ElTableColumn
            prop="base_url"
            :label="t('ai.health.columnBaseUrl')"
            min-width="180"
            show-overflow-tooltip
          />
          <ElTableColumn :label="t('ai.health.columnModels')" min-width="180">
            <template #default="{ row }">
              <span class="text-xs">{{ (row.models ?? []).join(', ') || '—' }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.health.columnEnabled')" width="100" align="center">
            <template #default="{ row }">
              <ElTag :type="row.enabled ? 'primary' : 'info'" size="small">
                {{ row.enabled ? t('ai.common.enabled') : t('ai.common.disabled') }}
              </ElTag>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :description="t('ai.health.sharedEmpty')" :image-size="80" />
          </template>
        </ElTable>
      </ElCard>

      <div class="grid gap-4 lg:grid-cols-2">
        <ElCard shadow="never">
          <template #header>
            <span class="z-label">{{ t('ai.health.cooldown') }}</span>
          </template>
          <ul v-if="Object.keys(data.cooldown_seconds ?? {}).length > 0" class="flex flex-col gap-1">
            <li
              v-for="(seconds, name) in data.cooldown_seconds"
              :key="name"
              class="flex items-center justify-between text-sm"
            >
              <span>{{ name }}</span>
              <span class="tabular-nums">{{ seconds }}</span>
            </li>
          </ul>
          <p v-else class="text-sm" style="color: var(--zv-label)">
            {{ t('ai.health.cooldownEmpty') }}
          </p>
        </ElCard>

        <ElCard shadow="never">
          <template #header>
            <span class="z-label">{{ t('ai.health.models') }}</span>
          </template>
          <div v-if="(data.models ?? []).length > 0" class="flex flex-wrap gap-1">
            <ElTag v-for="model in data.models" :key="model" size="small" type="info">
              {{ model }}
            </ElTag>
          </div>
          <p v-else class="text-sm" style="color: var(--zv-label)">
            {{ t('ai.health.modelsEmpty') }}
          </p>
        </ElCard>
      </div>
    </template>
  </div>
</template>

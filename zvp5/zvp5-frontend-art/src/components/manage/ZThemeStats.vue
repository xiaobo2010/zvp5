<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ElSkeleton } from 'element-plus/es/components/skeleton/index.mjs'
import api from '@/api'

const { t } = useI18n()

interface ThemeStatItem {
  id: string
  label: string
  count: number
  percent: number
}

interface ThemeStatsData {
  total: number
  items: ThemeStatItem[]
  other: number
}

const loading = ref(true)
const failed = ref(false)
const forbidden = ref(false)
const stats = ref<ThemeStatsData | null>(null)

const otherPercent = computed(() => {
  if (!stats.value || stats.value.total === 0) return 0
  return Math.round((stats.value.other / stats.value.total) * 100)
})

async function loadStats() {
  loading.value = true
  failed.value = false
  forbidden.value = false
  try {
    stats.value = await api.theme.getThemeStats()
  } catch (err: any) {
    if (err?.code === 403 || err?.status === 403) {
      forbidden.value = true
    } else {
      failed.value = true
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div class="theme-stats-container">
    <ElSkeleton v-if="loading" :rows="4" animated />

    <ElAlert
      v-else-if="forbidden"
      type="error"
      :title="t('manage.themes.noPermission')"
      :closable="false"
      show-icon
    />

    <ElAlert
      v-else-if="failed"
      type="error"
      :title="t('manage.themes.loadFailed')"
      :closable="false"
      show-icon
    />

    <div v-else-if="!stats || stats.items.length === 0" class="theme-stats-empty">
      {{ t('manage.themes.empty') }}
    </div>

    <div v-else class="theme-stats-content">
      <div class="theme-stats-summary">
        <span class="theme-stats-label">{{ t('manage.themes.total') }}</span>
        <span class="theme-stats-total">{{ stats.total }}</span>
      </div>

      <div class="theme-stats-subtitle">{{ t('manage.themes.subtitle') }}</div>

      <ul class="theme-stats-list">
        <li v-for="item in stats.items" :key="item.id" class="theme-stat-item">
          <div class="theme-stat-header">
            <span class="theme-stat-label">{{ item.label }}</span>
            <span class="theme-stat-value">{{ item.count }} ({{ item.percent }}%)</span>
          </div>
          <div class="theme-stat-bar">
            <div class="theme-stat-fill" :style="{ width: item.percent + '%' }" />
          </div>
        </li>

        <li v-if="stats.other > 0" class="theme-stat-item theme-stat-item--other">
          <div class="theme-stat-header">
            <span class="theme-stat-label">{{ t('manage.themes.other') }}</span>
            <span class="theme-stat-value">{{ stats.other }} ({{ otherPercent }}%)</span>
          </div>
          <div class="theme-stat-bar">
            <div class="theme-stat-fill" :style="{ width: otherPercent + '%' }" />
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.theme-stats-container {
  padding: var(--yg-space-4);
  color: var(--yg-text-primary);
}

.theme-stats-empty {
  padding: var(--yg-space-5);
  text-align: center;
  color: var(--yg-text-secondary);
}

.theme-stats-content {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

.theme-stats-summary {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
}

.theme-stats-label {
  font-size: 0.875rem;
  color: var(--yg-text-secondary);
}

.theme-stats-total {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--yg-color-primary);
}

.theme-stats-subtitle {
  font-size: 0.875rem;
  color: var(--yg-text-secondary);
}

.theme-stats-list {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.theme-stat-item {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

.theme-stat-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 0.875rem;
}

.theme-stat-label {
  font-weight: 500;
  color: var(--yg-text-primary);
}

.theme-stat-value {
  color: var(--yg-text-secondary);
  font-variant-numeric: tabular-nums;
}

.theme-stat-bar {
  height: 8px;
  border-radius: var(--yg-radius-xs);
  background: var(--yg-bg-surface-sunken);
  overflow: hidden;
}

.theme-stat-fill {
  height: 100%;
  background: linear-gradient(
    90deg,
    var(--yg-color-primary),
    color-mix(in srgb, var(--yg-color-primary) 80%, transparent)
  );
  border-radius: inherit;
  transition: width 400ms cubic-bezier(0.16, 1, 0.3, 1);
}

.theme-stat-item--other .theme-stat-fill {
  background: linear-gradient(
    90deg,
    var(--yg-text-tertiary),
    color-mix(in srgb, var(--yg-text-tertiary) 60%, transparent)
  );
}
</style>

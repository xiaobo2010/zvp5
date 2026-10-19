<script setup lang="ts">
import { ElIcon, ElProgress } from 'element-plus'
import { toRefs, computed } from 'vue'
import { ref, watch } from 'vue'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import type { ActivityMember } from 'types/activity.v2'

const props = defineProps<{
  type: ActivityMember['mode']
  standardTime?: number
  realTime: number
}>()

const { type, realTime: real } = toRefs(props)

const { t } = useI18n()

const standard = ref(0)

function refreshStandardTime() {
  if (type.value === 'social-practice') {
    standard.value = 18
  } else if (type.value === 'on-campus') {
    standard.value = 25
  } else {
    standard.value = 15
  }
}

watch(
  type,
  () => {
    refreshStandardTime()
  },
  { immediate: true }
)

const colors = [
  {
    color: 'var(--el-color-danger)',
    percentage: 25
  },
  {
    color: 'var(--el-color-primary)',
    percentage: 25.01
  },
  {
    color: 'var(--el-color-primary)',
    percentage: 99.99
  },
  {
    color: 'var(--el-color-success)',
    percentage: 100
  }
]

const percentage = computed(() => {
  if (!standard.value) return 0
  return Math.min((real.value / standard.value) * 100, 100)
})

function calculateLoss() {
  const loss = standard.value - real.value
  if (loss > 0) {
    return {
      icon: CaretBottom,
      class: 'lower',
      text: t('home.panels.time.lack'),
      loss: loss
    }
  } else {
    return {
      icon: CaretTop,
      class: 'upper',
      text: t('home.panels.time.exceed'),
      loss: -loss
    }
  }
}
</script>

<template>
  <div class="time-gauge">
    <ElProgress type="dashboard" :percentage="percentage" :color="colors" :width="128">
      <p class="gauge-time">{{ real }}h</p>
      <p class="gauge-label">{{ t(`activity.mode.${type}.name`) }}</p>
    </ElProgress>
    <div class="footer-item">
      <span>{{ real }} / {{ standard }} {{ t('home.panels.time.unit') }}</span>
      <span
        :class="calculateLoss().class"
        :aria-label="calculateLoss().text + calculateLoss().loss.toFixed(1) + t('home.panels.time.unit')"
      >
        <span>{{ calculateLoss().text }}</span>
        <span>{{ calculateLoss().loss.toFixed(1) }}</span>
        <ElIcon style="font-size: 12px"><Component :is="calculateLoss().icon" /></ElIcon>
      </span>
    </div>
  </div>
</template>

<style scoped>
.time-gauge {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  min-width: 128px;
}

.gauge-time {
  font-size: 20px;
  font-weight: bold;
}

.gauge-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.upper {
  color: var(--el-color-success);
}

.lower {
  color: var(--el-color-error);
}

.footer-item {
  font-size: 12px !important;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  color: var(--el-text-color-regular);
  gap: 4px;
}

.footer-item .upper,
.footer-item .lower {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
</style>

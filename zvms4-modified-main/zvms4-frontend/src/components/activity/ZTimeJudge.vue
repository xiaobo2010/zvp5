<script setup lang="ts">
import { ElIcon, ElProgress } from 'element-plus'
import { toRefs, computed } from 'vue'
import { ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
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

// 环形宽度跟随容器：卡片被压窄时整体等比收缩，避免环与左右两侧内容重叠。
const root = ref<HTMLElement>()
const { width: rootWidth } = useElementSize(root)
const ringSize = computed(() => {
  const width = rootWidth.value || 128
  return Math.round(Math.min(128, Math.max(80, width - 4)))
})

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

// 与首页三环共用类别色：校内=红、校外=绿、社会实践=蓝
const CATEGORY_COLOR: Record<string, string> = {
  'on-campus': 'var(--yg2-cat-school)',
  'off-campus': 'var(--yg2-cat-community)',
  'social-practice': 'var(--yg2-cat-time)'
}

const ringColor = computed(() => CATEGORY_COLOR[type.value] || 'var(--yg2-accent)')

const colors = computed(() => [
  { color: ringColor.value, percentage: 99.99 },
  { color: 'var(--yg2-cat-community)', percentage: 100 }
])

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
  <div ref="root" class="time-gauge">
    <ElProgress type="dashboard" :percentage="percentage" :color="colors" :width="ringSize">
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
  gap: 6px;
  width: 100%;
  min-width: 0;
  container-type: inline-size;
}

.gauge-time {
  /* 环内数字跟容器等比缩放，窄卡片下不再顶出环外 */
  font-size: clamp(13px, 15cqw, 20px);
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}

.gauge-label {
  margin-top: 2px;
  max-width: 100%;
  font-size: clamp(10px, 9cqw, 12px);
  line-height: 1.2;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.upper {
  color: var(--el-color-success);
}

.lower {
  color: var(--el-color-error);
}

.footer-item {
  display: flex;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px 8px;
  max-width: 100%;
  font-size: 11px !important;
  line-height: 1.35;
  text-align: center;
  color: var(--el-text-color-regular);
  font-variant-numeric: tabular-nums;
}

.footer-item .upper,
.footer-item .lower {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
</style>

<script setup lang="ts">
import { ElIcon, ElProgress } from 'element-plus'
import { computed, onUnmounted, ref, toRefs, watch } from 'vue'
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

const DEFAULT_DURATION = 450

const shownPercentage = ref(0)
const shownReal = ref(0)

const realDecimals = computed(() => {
  const text = String(real.value)
  const dot = text.indexOf('.')
  if (dot === -1) return 0
  return Math.min(text.length - dot - 1, 4)
})

const shownRealText = computed(() => shownReal.value.toFixed(realDecimals.value))

let frameId = 0
let startedAt = 0
let duration = DEFAULT_DURATION
let fromPercentage = 0
let toPercentage = 0
let fromReal = 0
let toReal = 0

function readPageDuration() {
  if (typeof window === 'undefined') return DEFAULT_DURATION
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--yg-dur-page').trim()
  const matched = /^([\d.]+)(ms|s)$/.exec(raw)
  if (!matched) return DEFAULT_DURATION
  const value = Number(matched[1])
  return matched[2] === 's' ? value * 1000 : value
}

function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

function easeOut(t: number) {
  return 1 - (1 - t) ** 3
}

function stopTween() {
  if (frameId) {
    cancelAnimationFrame(frameId)
    frameId = 0
  }
}

function settle() {
  shownPercentage.value = toPercentage
  shownReal.value = toReal
}

function step(now: number) {
  const progress = Math.min((now - startedAt) / duration, 1)
  const eased = easeOut(progress)
  shownPercentage.value = fromPercentage + (toPercentage - fromPercentage) * eased
  shownReal.value = fromReal + (toReal - fromReal) * eased
  if (progress < 1) {
    frameId = requestAnimationFrame(step)
    return
  }
  frameId = 0
  settle()
}

function sweep() {
  stopTween()
  toPercentage = percentage.value
  toReal = real.value
  if (prefersReducedMotion()) {
    settle()
    return
  }
  fromPercentage = shownPercentage.value
  fromReal = shownReal.value
  duration = readPageDuration()
  startedAt = performance.now()
  frameId = requestAnimationFrame(step)
}

watch([real, percentage], sweep, { immediate: true })

onUnmounted(stopTween)

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
    <ElProgress type="dashboard" :percentage="shownPercentage" :color="colors" :width="128">
      <p class="gauge-time yg-text-num">{{ shownRealText }}h</p>
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
  font-variant-numeric: tabular-nums;
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

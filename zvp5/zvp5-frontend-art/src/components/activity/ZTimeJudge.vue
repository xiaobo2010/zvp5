<script setup lang="ts">
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElProgress } from 'element-plus/es/components/progress/index.mjs'
import { computed, onMounted, onUnmounted, ref, toRefs, watch } from 'vue'
import { CaretTop, CaretBottom } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import type { ActivityMember } from 'types/activity.v2'

const props = defineProps<{
  type: ActivityMember['mode']
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

// 圆环交给 ElProgress 内置的 `stroke-dasharray 0.6s ease` 过渡：这里只在挂载后
// 写一次真实值完成扫入，之后每次数据变化直接改 prop，由 EP 从当前进度平滑过渡。
// 自己用 rAF 逐帧改 percentage 会让那条 CSS transition 每帧重启，圆环永远追不上。
// 数字计数器与 EP 的 0.6s 圆环同步收尾
const REAL_TWEEN_DURATION = 600

const shownPercentage = ref(prefersReducedMotion() ? percentage.value : 0)
const shownReal = ref(0)

const realDecimals = computed(() => {
  const text = String(real.value)
  const dot = text.indexOf('.')
  if (dot === -1) return 0
  return Math.min(text.length - dot - 1, 4)
})

const shownRealText = computed(() => shownReal.value.toFixed(realDecimals.value))

let realFrameId = 0
let realStartedAt = 0
let realFrom = 0
let realTo = 0
let ringArmed = false
let armFrameId = 0

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

function stopRealTween() {
  if (realFrameId) {
    cancelAnimationFrame(realFrameId)
    realFrameId = 0
  }
}

function stepReal(now: number) {
  const progress = Math.min((now - realStartedAt) / REAL_TWEEN_DURATION, 1)
  shownReal.value = realFrom + (realTo - realFrom) * easeOut(progress)
  if (progress < 1) {
    realFrameId = requestAnimationFrame(stepReal)
    return
  }
  realFrameId = 0
  shownReal.value = realTo
}

function sweepReal() {
  stopRealTween()
  realTo = real.value
  if (prefersReducedMotion()) {
    shownReal.value = realTo
    return
  }
  realFrom = shownReal.value
  realStartedAt = performance.now()
  realFrameId = requestAnimationFrame(stepReal)
}

watch(real, sweepReal, { immediate: true })

// 圆环扫描未开始前的数据变化不起帧，等挂载后的那一帧统一写入最新值
watch(percentage, (value) => {
  if (ringArmed) shownPercentage.value = value
})

onMounted(() => {
  if (prefersReducedMotion()) {
    ringArmed = true
    return
  }
  if (typeof requestAnimationFrame !== 'function') {
    ringArmed = true
    shownPercentage.value = percentage.value
    return
  }
  armFrameId = requestAnimationFrame(() => {
    armFrameId = 0
    ringArmed = true
    shownPercentage.value = percentage.value
  })
})

onUnmounted(() => {
  stopRealTween()
  if (armFrameId) {
    cancelAnimationFrame(armFrameId)
    armFrameId = 0
  }
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

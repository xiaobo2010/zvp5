<script setup lang="ts">
import { computed } from 'vue'

/**
 * 进度条策略（统一口径）：
 * - 不确定进度（不知道总量）：indeterminate 扫光；
 * - 已知进度：显示百分比 + 预计剩余时间（>10s 的上传必须给剩余时间）。
 */
const props = withDefaults(
  defineProps<{
    percent?: number | null
    label?: string
    /** 预计剩余秒数，null 表示算不出来 */
    remainingSeconds?: number | null
  }>(),
  { percent: null, label: '', remainingSeconds: null }
)

const indeterminate = computed(() => props.percent === null || props.percent === undefined)
const clamped = computed(() => Math.min(100, Math.max(0, props.percent ?? 0)))

function formatRemaining(seconds: number): string {
  if (seconds < 60) {
    return `剩余约 ${Math.ceil(seconds)} 秒`
  }
  const minutes = Math.floor(seconds / 60)
  return `剩余约 ${minutes} 分 ${Math.ceil(seconds % 60)} 秒`
}

const remainingText = computed(() =>
  props.remainingSeconds === null || props.remainingSeconds === undefined
    ? ''
    : formatRemaining(props.remainingSeconds)
)
</script>

<template>
  <div class="yg2-progress">
    <div class="yg2-progress__meta">
      <span>{{ label }}</span>
      <span>{{ indeterminate ? '' : `${Math.round(clamped)}%` }} {{ remainingText }}</span>
    </div>
    <div
      class="yg2-progress__track"
      role="progressbar"
      :aria-valuenow="indeterminate ? undefined : Math.round(clamped)"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div
        class="yg2-progress__bar"
        :class="{ 'yg2-progress__bar--indeterminate': indeterminate }"
        :style="indeterminate ? undefined : { width: `${clamped}%` }"
      />
    </div>
  </div>
</template>

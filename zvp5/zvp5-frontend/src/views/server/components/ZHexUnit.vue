<script setup lang="ts">
/**
 * MAGI 矩形数据单元。
 *
 * Phigros × EVA MAGI 设计语言：矩形数据舱带裁角，左侧垂直填充带，
 * 扫描线纹理，读数居中。每个单元承载一个关键指标：
 *   - 矩形徽章承载主读数（ON/OFF、连接池占用、剩余秒数…）
 *   - 右侧 HUD mono 标签给出通道名（SYS // DB），说明文字给出上下文
 *
 * 徽章用 clip-path 切出裁角，不依赖图片；tone 决定强调色，danger 态会呼吸。
 * 传入 progress（0–100）时，左侧垂直填充带按剩余比例从底部向上充能，
 * 连续平滑变化；不给 progress 时保持静态外观。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 通道标签，如 'SYS // DB' */
    label?: string
    /** 徽章里的主读数（短文本或数字） */
    value?: string | number
    /** 说明文字 */
    hint?: string
    /** 语气：accent 正常 / ok 良好 / warn 警告 / danger 危险 / idle 空闲 */
    tone?: 'accent' | 'ok' | 'warn' | 'danger' | 'idle'
    /** 徽章尺寸 */
    size?: 'sm' | 'md' | 'lg'
    /** 说明文字是否降噪（次要信息用 muted） */
    muted?: boolean
    /** 倒计时进度 0–100；给出时左侧垂直带按剩余比例填充，不给则不渲染 */
    progress?: number
  }>(),
  {
    label: '',
    value: '',
    hint: '',
    tone: 'accent',
    size: 'md',
    muted: false
  }
)

// 各档徽章的宽高与基准字号，样式真源在 css/phi-server.css 的 .phi-srv-hex--*
const SIZE_META: Record<string, { width: number; height: number; font: number }> = {
  sm: { width: 52, height: 36, font: 13 },
  md: { width: 64, height: 42, font: 16 },
  lg: { width: 84, height: 52, font: 22 }
}

/** 进度夹到 0–100；未传 progress 时为 null（不渲染填充层） */
const progressPct = computed(() => {
  if (props.progress === undefined || props.progress === null) return null
  const pct = Number(props.progress)
  if (!Number.isFinite(pct)) return null
  return Math.min(100, Math.max(0, pct))
})

/** 填充高度用 --phi-unit-p 传给 CSS，交给 @property 做 200ms 平滑过渡 */
const progressStyle = computed(() => {
  const pct = progressPct.value
  if (pct === null) return undefined
  return { '--phi-unit-p': `${pct}%` }
})

const badgeEl = ref<HTMLElement | null>(null)
const textEl = ref<HTMLElement | null>(null)
const fittedFont = ref<string | undefined>(undefined)

/**
 * 矩形数据舱的可用宽度：扣除左侧填充槽(10px) + 左右内边距(padding)。
 * 矩形数据舱不需要按六边形腰宽收窄。
 */
function safeWidth(w: number): number {
  // 左侧填充槽 10px，左右 padding 共约 18px（md: 10+8）
  return w - 28
}

/**
 * 长词读数（ONLINE / DEGRADED）需要收字号以防溢出。
 * 量出自然字宽再按比例收字号，既不溢出也不被省略号截断。
 */
function fitValue() {
  const badge = badgeEl.value
  const text = textEl.value
  if (!badge || !text) return
  const meta = SIZE_META[props.size] ?? SIZE_META.md
  const rect = badge.getBoundingClientRect()
  const w = rect.width || meta.width

  // 量自然字宽：inline-block 的收缩宽度会被可用宽度夹住，
  // 所以临时按 max-content + 基准字号量，量完把内联样式清掉
  // （字号最终由父级的 fittedFont 决定，内联覆盖会盖掉继承值）
  text.style.width = 'max-content'
  text.style.maxWidth = 'none'
  text.style.fontSize = `${meta.font}px`
  const natural = text.getBoundingClientRect().width
  text.style.width = ''
  text.style.maxWidth = ''
  text.style.fontSize = ''
  if (!natural) return

  const avail = safeWidth(w)
  let font = meta.font
  if (natural > avail) {
    font = Math.max(8, meta.font * (avail / natural) * 0.95)
  }
  fittedFont.value = `${Math.max(8, Math.min(meta.font, font)).toFixed(1)}px`
}

onMounted(() => {
  void nextTick(fitValue)
})

watch(
  () => [props.value, props.size],
  () => {
    void nextTick(fitValue)
  }
)
</script>

<template>
  <div class="phi-srv-unit">
    <span
      ref="badgeEl"
      class="phi-srv-hex"
      :class="[
        `phi-srv-hex--${tone}`,
        `phi-srv-hex--${size}`,
        { 'phi-srv-hex--progress': progressPct !== null }
      ]"
    >
      <span v-if="progressPct !== null" class="phi-srv-hex__progress" :style="progressStyle" />
      <span class="phi-srv-hex__value" :style="fittedFont ? { fontSize: fittedFont } : undefined">
        <span ref="textEl" class="phi-srv-hex__text">{{ props.value }}</span>
      </span>
    </span>
    <span class="phi-srv-unit__body">
      <span v-if="props.label" class="phi-srv-unit__label">{{ props.label }}</span>
      <span v-if="props.hint" class="phi-srv-unit__hint" :class="{ 'phi-srv-unit__hint--muted': props.muted }">
        {{ props.hint }}
      </span>
    </span>
  </div>
</template>

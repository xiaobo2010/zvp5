<script setup lang="ts">
/**
 * MAGI 六边形消息单元。
 *
 * EVA MAGI 的状态面板由一块块六边形「消息单元」拼成：每块自己带一个
 * 状态读数与一行说明。这里把那块单元抽成组件，服务器台各分区共用：
 *   - 六边形徽章承载最重要的那一个数（ON/OFF、连接池占用、剩余秒数…）
 *   - 右侧 HUD mono 标签给出通道名（SYS // DB），说明文字给出上下文
 *
 * 徽章用 clip-path 切出，不依赖图片；tone 决定强调色，danger 态会呼吸。
 * 传入 progress（0–100）时，徽章外圈变成表盘：亮弧从正上方顺时针按剩余
 * 比例连续收缩，六边形自身就成了倒计时指示器；不给 progress 时保持静态外观。
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
    /** 倒计时进度 0–100；给出时六边形外圈按剩余比例填充，不给则不渲染 */
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
  sm: { width: 40, height: 44, font: 12 },
  md: { width: 56, height: 62, font: 15 },
  lg: { width: 78, height: 86, font: 22 }
}

/** 进度夹到 0–100；未传 progress 时为 null（不渲染填充层） */
const progressPct = computed(() => {
  if (props.progress === undefined || props.progress === null) return null
  const pct = Number(props.progress)
  if (!Number.isFinite(pct)) return null
  return Math.min(100, Math.max(0, pct))
})

/** 扇形角度用 --phi-hex-p 传给 CSS，交给 @property 做 200ms 平滑过渡 */
const progressStyle = computed(() => {
  const pct = progressPct.value
  if (pct === null) return undefined
  return { '--phi-hex-p': `${pct}%` }
})

const hexEl = ref<HTMLElement | null>(null)
const textEl = ref<HTMLElement | null>(null)
const fittedFont = ref<string | undefined>(undefined)

/**
 * 六边形越往上下越窄，读数只能占中间那条「腰」。
 * 六边形左右顶点在中线，向上下各收 26% 宽度：
 * 距中线 d（占高度比例）处的可用宽度 = W × (1 − 1.04d)。
 */
function safeWidth(w: number, h: number, font: number): number {
  const band = font * 0.72 // 大写字面高度
  const d = band / 2 / h
  return w * (1 - 1.04 * d) * 0.97
}

/**
 * 长词读数（ONLINE / DEGRADED）在六边形里放不下。
 * 量出自然字宽再按比例收字号，既不溢出也不被省略号截断。
 * 收窄后字带变矮、安全宽度变大，所以迭代几轮收敛。
 */
function fitValue() {
  const hex = hexEl.value
  const text = textEl.value
  if (!hex || !text) return
  const meta = SIZE_META[props.size] ?? SIZE_META.md
  const rect = hex.getBoundingClientRect()
  const w = rect.width || meta.width
  const h = rect.height || meta.height

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

  let font = meta.font
  for (let i = 0; i < 4 && font > 8; i += 1) {
    const scaled = natural * (font / meta.font)
    const avail = safeWidth(w, h, font)
    if (scaled <= avail) break
    font = Math.max(8, font * (avail / scaled))
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
      ref="hexEl"
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

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'

const props = withDefaults(
  defineProps<{
    title: string
    reason: string
    until: string
    appeal?: string
    countdownText?: string
    countdown?: number
    countdownTotal?: number
    ackText?: string
    variant?: 'account' | 'device'
    lockScroll?: boolean
    /** 风险格点亮数量（0–12），不传时按 variant 取默认值 */
    riskLevel?: number
    /** 挂载时剩余秒数，到点进入消散；0 立即消散。仅读取挂载时的值 */
    autoDismiss?: number
    /**
     * 预热模式：挂载后不可见、不锁滚动、不抢焦点、不计时消散。
     * 登录页空闲时用它提前跑一遍首帧渲染成本，真正封禁时不再掉帧。
     */
    prewarm?: boolean
  }>(),
  {
    appeal: '',
    countdownText: '',
    countdown: 0,
    countdownTotal: 0,
    ackText: '',
    variant: 'account',
    lockScroll: true,
    riskLevel: undefined,
    autoDismiss: 15,
    prewarm: false
  }
)

const emit = defineEmits<{ ack: [] }>()

const { t, te } = useI18n()

// 与 ZLoginReveal 一致：挂载时读取一次，减少动效用户直接看到静态画面
const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true

const RISK_CELLS = 12
const PARTICLE_COUNT = 80
const DISMISS_DURATION_FULL = 1200 // 完整消散动画时长 ms
const DISMISS_DURATION_REDUCED = 300 // reduced-motion 淡出时长 ms

const uid = useId()
const titleId = `z-ban-title-${uid}`
const reasonId = `z-ban-reason-${uid}`

const rootRef = ref<HTMLElement | null>(null)
const screenRef = ref<HTMLElement | null>(null)
const particleContainerRef = ref<HTMLElement | null>(null)

const isDismissing = ref(false)

// 可选文案键由其他分支补充；缺失时回退到英文装饰文字
function label(key: string, fallback: string): string {
  return te(key) ? t(key) : fallback
}

const warningText = computed(() => label('nav.banned.warning', 'WARNING'))
const systemText = computed(() => label('nav.banned.system', 'SYSTEM LOCK'))
const reasonLabel = computed(() => label('nav.banned.reasonLabel', 'REASON'))
const riskLabel = computed(() => label('nav.banned.risk', 'RISK LEVEL'))
const variantCode = computed(() =>
  props.variant === 'device'
    ? label('nav.banned.codeDevice', 'DEVICE · 0xD3')
    : label('nav.banned.codeAccount', 'ACCOUNT · 0xA1')
)
const overheatText = computed(() => {
  const locked =
    props.variant === 'device'
      ? label('nav.banned.lockedDevice', 'DEVICE LOCKED')
      : label('nav.banned.lockedAccount', 'ACCOUNT LOCKED')
  return `${label('nav.banned.overheat', 'OVERHEAT')} // ${locked}`
})

const litCells = computed(() => {
  const fallback = props.variant === 'device' ? 10 : RISK_CELLS
  const raw = props.riskLevel ?? fallback
  return Math.max(0, Math.min(RISK_CELLS, Math.round(raw)))
})

const showCountdown = computed(() => props.countdown > 0)
const initialCountdown = props.countdown
const countdownRatio = computed(() => {
  const total = props.countdownTotal > 0 ? props.countdownTotal : initialCountdown
  if (total <= 0) return 0
  return Math.max(0, Math.min(1, props.countdown / total))
})
const countdownLabel = computed(() => props.countdownText || `${props.countdown}s`)

let previousOverflow: string | null = null
let dismissTimer: ReturnType<typeof setTimeout> | null = null

interface Particle {
  id: string
  x: number
  y: number
  size: number
  color: string
  tx: number
  ty: number
  delay: number
}

const particles = ref<Particle[]>([])

function generateParticles(): void {
  if (!screenRef.value) return

  const rect = screenRef.value.getBoundingClientRect()
  const centerX = rect.width / 2
  const centerY = rect.height / 2

  // 采样关键区域作为粒子源
  const sources: Array<{ x: number; y: number; weight: number }> = []

  // 警告文字区域
  const warningEl = screenRef.value.querySelector('.z-ban__warning')
  if (warningEl) {
    const r = warningEl.getBoundingClientRect()
    sources.push({
      x: r.left - rect.left + r.width / 2,
      y: r.top - rect.top + r.height / 2,
      weight: 0.3
    })
  }

  // 原因块
  const reasonEl = screenRef.value.querySelector('.z-ban__reason')
  if (reasonEl) {
    const r = reasonEl.getBoundingClientRect()
    sources.push({
      x: r.left - rect.left + r.width / 2,
      y: r.top - rect.top + r.height / 2,
      weight: 0.25
    })
  }

  // 风险格
  const cellsEl = screenRef.value.querySelector('.z-ban__cells')
  if (cellsEl) {
    const r = cellsEl.getBoundingClientRect()
    sources.push({
      x: r.left - rect.left + r.width / 2,
      y: r.top - rect.top + r.height / 2,
      weight: 0.2
    })
  }

  // 边缘红色面板（上下各一个源）
  sources.push({ x: centerX, y: 40, weight: 0.15 })
  sources.push({ x: centerX, y: rect.height - 40, weight: 0.1 })

  const colors = ['#ff3b30', '#ffffff', '#ffb59e', '#ff7875', '#ffd591']

  const newParticles: Particle[] = []
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    // 按权重随机选择源
    const rand = Math.random()
    let cumulative = 0
    let source = sources[0]
    for (const s of sources) {
      cumulative += s.weight
      if (rand <= cumulative) {
        source = s
        break
      }
    }

    // 起点加入随机偏移
    const x = source.x + (Math.random() - 0.5) * 80
    const y = source.y + (Math.random() - 0.5) * 60

    // 终点：向上方偏移 + 横向随机
    const tx = x + (Math.random() - 0.5) * 300
    const ty = y - 200 - Math.random() * 400

    newParticles.push({
      id: `p-${i}`,
      x,
      y,
      size: 2 + Math.random() * 4,
      color: colors[Math.floor(Math.random() * colors.length)],
      tx,
      ty,
      delay: Math.random() * 200
    })
  }

  particles.value = newParticles
}

let ackTimer: ReturnType<typeof setTimeout> | null = null

function startDismiss(): void {
  if (isDismissing.value) return
  isDismissing.value = true

  // reduced-motion 只做短淡出；否则一次性测量布局生成粒子
  if (!reducedMotion) generateParticles()
  ackTimer = setTimeout(
    () => {
      ackTimer = null
      emit('ack')
    },
    reducedMotion ? DISMISS_DURATION_REDUCED : DISMISS_DURATION_FULL
  )
}

/** 挂载时剩余秒数：非有限值回退 15，负数按 0（立即消散）处理 */
function initialDelayMs(): number {
  const raw = props.autoDismiss
  const seconds = Number.isFinite(raw) ? Math.max(0, raw) : 15
  return seconds * 1000
}

onMounted(() => {
  // 预热实例只是一次离屏渲染，不接管滚动、焦点与计时。
  if (props.prewarm) {
    return
  }
  if (props.lockScroll) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }

  void nextTick(() => {
    // 聚焦对话框本身，避免焦点留在被遮挡的页面上
    rootRef.value?.focus({ preventScroll: true })
  })

  // 只读取挂载时的 autoDismiss（剩余秒数）；父组件之后递减该 prop 不会重置计时
  const delay = initialDelayMs()
  if (delay === 0) {
    startDismiss()
  } else {
    dismissTimer = setTimeout(() => {
      dismissTimer = null
      startDismiss()
    }, delay)
  }
})

onBeforeUnmount(() => {
  if (previousOverflow !== null) {
    document.body.style.overflow = previousOverflow
    previousOverflow = null
  }
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer)
    dismissTimer = null
  }
  if (ackTimer !== null) {
    clearTimeout(ackTimer)
    ackTimer = null
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      ref="rootRef"
      class="z-ban hidden-print"
      :class="[
        `z-ban--${variant}`,
        { 'is-reduced': reducedMotion, 'is-dismissing': isDismissing, 'is-prewarm': prewarm }
      ]"
      role="alertdialog"
      :aria-modal="prewarm ? undefined : 'true'"
      :aria-hidden="prewarm || undefined"
      :inert="prewarm || undefined"
      :aria-labelledby="titleId"
      :aria-describedby="reasonId"
      tabindex="-1"
      @contextmenu.prevent
      @keydown.tab.prevent
    >
      <div ref="screenRef" class="z-ban__screen">
        <!-- 六边形蜂窝线框：压在最底层，只做仪器感底纹，不参与布局 -->
        <ZHexGrid
          :size="58"
          color="var(--z-ban-red)"
          :opacity="0.1"
          drift
          flicker
          :z-index="-1"
        />
        <div class="z-ban__hazard z-ban__hazard--top" aria-hidden="true" />
        <div class="z-ban__content">
          <p class="z-ban__sys" aria-hidden="true">
            <span class="z-ban__dot" />{{ systemText }} · {{ variantCode }}
          </p>
          <p class="z-ban__warning" aria-hidden="true">{{ warningText }}</p>
          <p class="z-ban__overheat" aria-hidden="true">{{ overheatText }}</p>
          <h2 :id="titleId" class="z-ban__title">{{ title }}</h2>

          <div class="z-ban__block">
            <span class="z-ban__label">{{ reasonLabel }}</span>
            <p :id="reasonId" class="z-ban__reason">{{ reason }}</p>
          </div>

          <div class="z-ban__block">
            <span class="z-ban__label">
              {{ riskLabel }}<span class="z-ban__risk-num">{{ litCells }}/{{ RISK_CELLS }}</span>
            </span>
            <div class="z-ban__cells" role="img" :aria-label="`${riskLabel} ${litCells}/${RISK_CELLS}`">
              <span
                v-for="n in RISK_CELLS"
                :key="n"
                class="z-ban__cell"
                :class="{ 'is-lit': n <= litCells }"
              />
            </div>
          </div>

          <p class="z-ban__until">{{ until }}</p>
          <p v-if="appeal" class="z-ban__appeal">{{ appeal }}</p>

          <div v-if="showCountdown" class="z-ban__countdown" role="timer">
            <div class="z-ban__bar" aria-hidden="true">
              <span class="z-ban__bar-fill" :style="{ transform: `scaleX(${countdownRatio})` }" />
            </div>
            <span class="z-ban__count">{{ countdownLabel }}</span>
          </div>
        </div>
        <div class="z-ban__hazard z-ban__hazard--bottom" aria-hidden="true" />
      </div>

      <div class="z-ban__panel z-ban__panel--top" aria-hidden="true" />
      <div class="z-ban__panel z-ban__panel--bottom" aria-hidden="true" />
      <div class="z-ban__seam" aria-hidden="true" />

      <!-- 粒子容器 -->
      <div
        v-if="isDismissing && !reducedMotion"
        ref="particleContainerRef"
        class="z-ban__particles"
        aria-hidden="true"
      >
        <span
          v-for="p in particles"
          :key="p.id"
          class="z-ban__particle"
          :style="{
            left: `${p.x}px`,
            top: `${p.y}px`,
            width: `${p.size}px`,
            height: `${p.size}px`,
            backgroundColor: p.color,
            '--tx': `${p.tx}px`,
            '--ty': `${p.ty}px`,
            animationDelay: `${p.delay}ms`
          }"
        />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 时间线：0–260ms 红线展开 → 保持 → 700–1250ms 面板分开；内容 900ms 起淡入上移；之后静止 */
.z-ban {
  --z-ban-red: var(--yg-red-500, #ff3b30);
  --z-ban-red-deep: var(--yg-red-900, #7a000c);
  --z-ban-ink: #0a0000;
  --z-ban-well: #22000a;
  --z-ban-edge: #5a000a;
  --z-ban-ease: cubic-bezier(0.76, 0, 0.24, 1);
  position: fixed;
  inset: 0;
  z-index: 10000;
  overflow: hidden;
  color: var(--yg-color-on-danger, #fff);
  font-family: var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  outline: none;
  user-select: none;
  overscroll-behavior: contain;
}

/* 预热实例：全屏但不可见，仍会走完样式解析与首帧合成，成本提前付掉；
   登录页 1.6s 后随组件卸载，不占常驻内存。 */
.z-ban.is-prewarm {
  opacity: 0;
  pointer-events: none;
}

.z-ban__screen {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  /* 建立层叠上下文：蜂窝底纹 z-index:-1 落在屏幕底色之上、内容之下 */
  isolation: isolate;
  background:
    radial-gradient(ellipse 90% 60% at 50% 38%, #3d0008 0%, transparent 70%),
    var(--z-ban-ink);
}

/* 红色扫描线：静态纹理，不做逐帧动画 */
.z-ban__screen::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: repeating-linear-gradient(
    0deg,
    rgb(255 59 48 / 0.07) 0 1px,
    transparent 1px 3px
  );
}

/* 警示斜纹带：上下各一条，静态 */
.z-ban__hazard {
  position: relative;
  flex: none;
  height: 14px;
  overflow: hidden;
  opacity: 0.85;
}

/* 斜纹单独一层：比条子左右各宽 48px，平移一个周期（24px 沿 45° 方向）
   时会露出边缘，靠这圈余量吃掉；只动 transform，走合成层。 */
.z-ban__hazard::before {
  content: '';
  position: absolute;
  inset: 0 -48px;
  background: repeating-linear-gradient(
    -45deg,
    var(--z-ban-red) 0 12px,
    var(--z-ban-ink) 12px 24px
  );
  animation: z-ban-hazard-crawl 3.4s linear infinite;
}

@keyframes z-ban-hazard-crawl {
  from {
    transform: translate3d(0, 0, 0);
  }
  to {
    transform: translate3d(-16.97px, 16.97px, 0);
  }
}

.z-ban__hazard--top {
  box-shadow: 0 4px 18px rgb(255 59 48 / 0.35);
}

.z-ban__hazard--bottom {
  box-shadow: 0 -4px 18px rgb(255 59 48 / 0.35);
}

.z-ban__content {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  width: 100%;
  max-width: 640px;
  margin: 0 auto;
  padding: clamp(20px, 5vh, 48px) 24px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* 内容超过视口时不要让 flex 居中把顶部裁掉；safe center 不可用时回退到 center */
  justify-content: safe center;
  gap: 16px;
  box-sizing: border-box;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  overflow-wrap: anywhere;
  user-select: text;
  animation: z-ban-content 420ms cubic-bezier(0.2, 0.8, 0.2, 1) 900ms both;
}

.z-ban__content > * {
  margin: 0;
}
.z-ban__sys {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  letter-spacing: 0.18em;
  color: var(--yg-red-300, #ffa39e);
  text-transform: uppercase;
}

.z-ban__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--z-ban-red);
  box-shadow: 0 0 8px var(--z-ban-red);
}

.z-ban__warning {
  font-size: clamp(44px, 15vw, 112px);
  font-weight: 800;
  line-height: 0.92;
  letter-spacing: 0.04em;
  color: var(--z-ban-red);
  text-shadow:
    0 0 18px rgb(255 59 48 / 0.55),
    3px 0 0 rgb(122 0 12 / 0.9);
  overflow-wrap: normal;
  word-break: keep-all;
  max-width: 100%;
  animation: z-ban-glitch-warning 9s steps(1, end) infinite;
}

/* 紧贴 WARNING 的过热状态行：单行，过长时省略而不撑破布局 */
.z-ban__overheat {
  margin-top: -8px;
  max-width: 100%;
  min-width: 0;
  font-family: var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: var(--z-ban-red);
  text-shadow: 0 0 8px rgb(255 59 48 / 0.45);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: z-ban-glitch-overheat 7.6s steps(1, end) infinite;
}

.z-ban__title {
  font-size: clamp(20px, 5.4vw, 28px);
  font-weight: 700;
  line-height: 1.3;
  color: #fff;
  border-left: 4px solid var(--z-ban-red);
  padding-left: 12px;
  animation: z-ban-glitch-title 11s steps(1, end) infinite;
}

/* 故障抖动：只用本页红/白做 CRT 信号错位，不引入青-品红色差。
   全程 transform 合成层 + 极稀疏的跳帧（90% 时间静止），像设备失控而非装饰。 */
@keyframes z-ban-glitch-warning {
  0%,
  88%,
  94%,
  100% {
    transform: none;
    text-shadow:
      0 0 18px rgb(255 59 48 / 0.55),
      3px 0 0 rgb(122 0 12 / 0.9);
  }
  89% {
    transform: translate3d(-3px, 0, 0) skewX(-1deg);
    text-shadow:
      -4px 0 0 var(--z-ban-red),
      4px 0 0 rgb(255 255 255 / 0.32);
  }
  90% {
    transform: translate3d(2px, 0, 0) skewX(0.8deg);
    text-shadow:
      4px 0 0 var(--z-ban-red),
      -3px 0 0 rgb(255 255 255 / 0.26);
  }
  91% {
    transform: translate3d(-1px, 0, 0);
  }
  92% {
    transform: none;
  }
  93% {
    transform: translate3d(2px, 0, 0);
  }
}

@keyframes z-ban-glitch-overheat {
  0%,
  90%,
  100% {
    transform: none;
    text-shadow: 0 0 8px rgb(255 59 48 / 0.45);
  }
  91% {
    transform: translate3d(3px, 0, 0);
    text-shadow:
      -3px 0 0 var(--z-ban-red),
      3px 0 0 rgb(255 255 255 / 0.3);
  }
  92% {
    transform: translate3d(-2px, 0, 0);
    text-shadow: 3px 0 0 rgb(255 120 115 / 0.85);
  }
  94% {
    transform: none;
    text-shadow: 0 0 8px rgb(255 59 48 / 0.45);
  }
}

@keyframes z-ban-glitch-title {
  0%,
  91%,
  100% {
    transform: none;
    text-shadow: none;
  }
  92% {
    transform: translate3d(-1px, 0, 0);
    text-shadow: -2px 0 0 var(--z-ban-red);
  }
  93% {
    transform: translate3d(1px, 0, 0);
    text-shadow: 2px 0 0 rgb(255 59 48 / 0.75);
  }
  94% {
    transform: none;
    text-shadow: none;
  }
}

.z-ban__block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.z-ban__label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--yg-red-400, #ff7875);
}

.z-ban__risk-num {
  font-variant-numeric: tabular-nums;
  color: #fff;
}

.z-ban__reason {
  margin: 0;
  padding: 14px 16px;
  font-size: clamp(16px, 4.4vw, 20px);
  font-weight: 700;
  line-height: 1.5;
  color: #fff;
  background: var(--z-ban-well);
  border: 1px solid var(--z-ban-edge);
  border-radius: 0;
  box-shadow: inset 0 0 0 1px rgb(255 59 48 / 0.12);
  white-space: pre-wrap;
}

.z-ban__cells {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 4px;
}

.z-ban__cell {
  height: 14px;
  border-radius: 0;
  background: #2a0006;
  border: 1px solid var(--z-ban-edge);
}

.z-ban__cell.is-lit {
  background: var(--z-ban-red);
  border-color: var(--yg-red-400, #ff7875);
  box-shadow: 0 0 6px rgb(255 59 48 / 0.6);
}

.z-ban__until {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--yg-red-300, #ffa39e);
  font-variant-numeric: tabular-nums;
}

.z-ban__appeal {
  font-size: 13px;
  line-height: 1.6;
  color: rgb(255 255 255 / 0.72);
}

.z-ban__countdown {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.z-ban__bar {
  position: relative;
  flex: 1 1 auto;
  height: 6px;
  overflow: hidden;
  border-radius: 0;
  background: #2a0006;
  border: 1px solid var(--z-ban-edge);
}

.z-ban__bar-fill {
  position: absolute;
  inset: 0;
  background: var(--z-ban-red);
  transform-origin: left center;
  transition: transform 1s linear;
}

.z-ban__count {
  flex: none;
  max-width: 60%;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  font-variant-numeric: tabular-nums;
}

/* 两块面板：与 ZLoginReveal 相同的开合结构，深红实色 */
.z-ban__panel {
  position: absolute;
  left: 0;
  right: 0;
  height: 50.2%;
  background: var(--z-ban-red-deep);
  will-change: transform;
  pointer-events: none;
}

.z-ban__panel--top {
  top: 0;
  background: linear-gradient(180deg, #3d0006 0%, var(--z-ban-red-deep) 100%);
  animation: z-ban-panel-top 550ms var(--z-ban-ease) 700ms forwards;
}

.z-ban__panel--bottom {
  bottom: 0;
  background: linear-gradient(0deg, #3d0006 0%, var(--z-ban-red-deep) 100%);
  animation: z-ban-panel-bottom 550ms var(--z-ban-ease) 700ms forwards;
}

/* 中缝红线：先从中心展开，面板分开时淡出 */
.z-ban__seam {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 2px;
  margin-top: -1px;
  background: #ff6a60;
  box-shadow:
    0 0 10px 2px rgb(255 59 48 / 0.85),
    0 0 36px 6px rgb(255 59 48 / 0.45);
  transform: scaleX(0);
  will-change: transform, opacity;
  pointer-events: none;
  animation: z-ban-seam 900ms linear forwards;
}

@keyframes z-ban-seam {
  0% { transform: scaleX(0); opacity: 1; animation-timing-function: cubic-bezier(0.2, 0.8, 0.2, 1); }
  29% { transform: scaleX(1); opacity: 1; }
  78% { transform: scaleX(1); opacity: 1; }
  100% { transform: scaleX(1); opacity: 0; }
}

@keyframes z-ban-panel-top {
  to { transform: translate3d(0, -100%, 0); }
}

@keyframes z-ban-panel-bottom {
  to { transform: translate3d(0, 100%, 0); }
}

@keyframes z-ban-content {
  from { opacity: 0; transform: translate3d(0, 16px, 0); }
  to { opacity: 1; transform: none; }
}

/* === 消散动效 === */

/* 消散时的脉冲效果：红色元素亮度闪烁 */
.z-ban.is-dismissing .z-ban__screen {
  animation: z-ban-dismiss-pulse 200ms ease-out;
}

.z-ban.is-dismissing .z-ban__hazard,
.z-ban.is-dismissing .z-ban__panel {
  animation: z-ban-dismiss-pulse 200ms ease-out;
}

/* 整屏淡出（完整动画） */
.z-ban.is-dismissing:not(.is-reduced) {
  animation: z-ban-dismiss-fade 200ms ease-out 1000ms forwards;
}

/* 整屏淡出（reduced-motion） */
.z-ban.is-dismissing.is-reduced {
  animation: z-ban-dismiss-fade-fast 300ms ease-out forwards;
}

@keyframes z-ban-dismiss-pulse {
  0%, 100% { filter: brightness(1); }
  50% { filter: brightness(1.4) saturate(1.3); }
}

@keyframes z-ban-dismiss-fade {
  to { opacity: 0; }
}

@keyframes z-ban-dismiss-fade-fast {
  to { opacity: 0; }
}

/* 粒子容器 */
.z-ban__particles {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
}

/* 单个粒子 */
.z-ban__particle {
  position: absolute;
  border-radius: 50%;
  box-shadow: 0 0 4px currentColor;
  will-change: transform, opacity;
  animation: z-ban-particle-drift 1200ms cubic-bezier(0.2, 0.6, 0.3, 1) forwards;
}

@keyframes z-ban-particle-drift {
  0% {
    opacity: 1;
    transform: translate(0, 0) scale(1);
  }
  20% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: translate(var(--tx), var(--ty)) scale(0.3);
  }
}

@media (max-width: 480px) {
  .z-ban__content {
    padding: 18px 16px;
    gap: 12px;
  }

  .z-ban__sys {
    font-size: 11px;
    letter-spacing: 0.12em;
  }

  .z-ban__cells {
    gap: 3px;
  }
}

/* 减少动效：不渲染面板与红线，内容直接静态显示 */
.z-ban.is-reduced .z-ban__panel,
.z-ban.is-reduced .z-ban__seam {
  display: none;
}

.z-ban.is-reduced .z-ban__content {
  animation: none;
}

.z-ban.is-reduced .z-ban__bar-fill {
  transition: none;
}

/* 静态化用户：去掉斜纹爬动与字体抖动，保留静态底纹 */
.z-ban.is-reduced .z-ban__hazard::before,
.z-ban.is-reduced .z-ban__warning,
.z-ban.is-reduced .z-ban__overheat,
.z-ban.is-reduced .z-ban__title {
  animation: none;
}

@media (prefers-reduced-motion: reduce) {
  .z-ban__panel,
  .z-ban__seam {
    display: none;
  }

  .z-ban__content {
    animation: none;
  }

  .z-ban__bar-fill {
    transition: none;
  }

  .z-ban__hazard::before,
  .z-ban__warning,
  .z-ban__overheat,
  .z-ban__title {
    animation: none;
  }
}

@media print {
  .z-ban {
    display: none !important;
  }
}
</style>

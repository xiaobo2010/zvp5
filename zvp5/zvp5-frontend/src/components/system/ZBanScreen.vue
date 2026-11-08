<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'

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
  }>(),
  {
    appeal: '',
    countdownText: '',
    countdown: 0,
    countdownTotal: 0,
    ackText: '',
    variant: 'account',
    lockScroll: true,
    riskLevel: undefined
  }
)

const emit = defineEmits<{ ack: [] }>()

const { t, te } = useI18n()

// 与 ZLoginReveal 一致：挂载时读取一次，减少动效用户直接看到静态画面
const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true

const RISK_CELLS = 12
const uid = useId()
const titleId = `z-ban-title-${uid}`
const reasonId = `z-ban-reason-${uid}`

const rootRef = ref<HTMLElement | null>(null)
const ackRef = ref<HTMLButtonElement | null>(null)

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

const showAck = computed(() => props.ackText.trim().length > 0)

let previousOverflow: string | null = null

onMounted(() => {
  if (props.lockScroll) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  void nextTick(() => {
    // 有确认按钮时聚焦按钮，否则聚焦对话框本身，避免焦点留在被遮挡的页面上
    ;(ackRef.value ?? rootRef.value)?.focus({ preventScroll: true })
  })
})

onBeforeUnmount(() => {
  if (previousOverflow !== null) {
    document.body.style.overflow = previousOverflow
    previousOverflow = null
  }
})

function onAck(): void {
  emit('ack')
}
</script>

<template>
  <Teleport to="body">
    <div
      ref="rootRef"
      class="z-ban hidden-print"
      :class="[`z-ban--${variant}`, { 'is-reduced': reducedMotion }]"
      role="alertdialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="reasonId"
      tabindex="-1"
      @contextmenu.prevent
      @keydown.tab.prevent
    >
      <div class="z-ban__screen">
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

          <button v-if="showAck" ref="ackRef" type="button" class="z-ban__ack" @click="onAck">
            {{ ackText }}
          </button>
        </div>
        <div class="z-ban__hazard z-ban__hazard--bottom" aria-hidden="true" />
      </div>

      <div class="z-ban__panel z-ban__panel--top" aria-hidden="true" />
      <div class="z-ban__panel z-ban__panel--bottom" aria-hidden="true" />
      <div class="z-ban__seam" aria-hidden="true" />
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

.z-ban__screen {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
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
  background: repeating-linear-gradient(
    -45deg,
    var(--z-ban-red) 0 12px,
    var(--z-ban-ink) 12px 24px
  );
  opacity: 0.85;
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
}

.z-ban__title {
  font-size: clamp(20px, 5.4vw, 28px);
  font-weight: 700;
  line-height: 1.3;
  color: #fff;
  border-left: 4px solid var(--z-ban-red);
  padding-left: 12px;
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
.z-ban__ack {
  align-self: stretch;
  min-height: 44px;
  margin-top: 4px;
  padding: 10px 20px;
  font: inherit;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--yg-color-on-danger, #fff);
  background: var(--yg-red-600, #e53026);
  border: 1px solid var(--yg-red-400, #ff7875);
  border-radius: 0;
  box-shadow: 0 6px 20px rgb(255 59 48 / 0.35);
  cursor: pointer;
  transition:
    transform 160ms ease,
    opacity 160ms ease;
}

.z-ban__ack:hover {
  opacity: 0.92;
}

.z-ban__ack:active {
  transform: translateY(1px);
}

.z-ban__ack:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 3px;
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

  .z-ban__ack {
    min-height: 48px;
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

.z-ban.is-reduced .z-ban__bar-fill,
.z-ban.is-reduced .z-ban__ack {
  transition: none;
}

@media (prefers-reduced-motion: reduce) {
  .z-ban__panel,
  .z-ban__seam {
    display: none;
  }

  .z-ban__content {
    animation: none;
  }

  .z-ban__bar-fill,
  .z-ban__ack {
    transition: none;
  }
}

@media print {
  .z-ban {
    display: none !important;
  }
}
</style>

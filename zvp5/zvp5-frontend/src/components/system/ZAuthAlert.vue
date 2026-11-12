<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    /** danger = 红色（高危，密码错误一类）；warning = 琥珀色（警示，无权限一类） */
    variant?: 'danger' | 'warning'
    title: string
    message: string
    hint?: string
    /** 次要按钮文案，空则不渲染 */
    ackText?: string
    /** 主按钮文案，空则不渲染 */
    actionText?: string
  }>(),
  {
    variant: 'danger',
    hint: '',
    ackText: '',
    actionText: ''
  }
)

const emit = defineEmits<{ ack: []; action: [] }>()

// 与 ZBanScreen / ZLoginReveal 一致：挂载时读一次，减少动效用户直接看静态画面
const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true

const uid = useId()
const titleId = `z-alert-title-${uid}`
const messageId = `z-alert-message-${uid}`

const rootRef = ref<HTMLElement | null>(null)
const actionRef = ref<HTMLButtonElement | null>(null)
const ackRef = ref<HTMLButtonElement | null>(null)

// 装饰串只表达「访问被拦」这件事，具体业务语义由调用方注入的 title/message 承担
const decorCode = computed(() =>
  props.variant === 'warning' ? 'ACCESS // WARNING' : 'ACCESS // DENIED'
)

// 主色以行内自定义属性下发：既方便调用方覆盖，也避免每个变体复制一整套样式
const accentVar = computed(() =>
  props.variant === 'warning' ? 'var(--yg-orange-500)' : 'var(--yg-red-500)'
)

const showAck = computed(() => props.ackText.trim().length > 0)
const showAction = computed(() => props.actionText.trim().length > 0)

let previousOverflow: string | null = null

onMounted(() => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  void nextTick(() => {
    // 优先级：主按钮 > 次要按钮 > 对话框本身，避免焦点留在被遮挡的登录页上
    ;(actionRef.value ?? ackRef.value ?? rootRef.value)?.focus({ preventScroll: true })
  })
})

onBeforeUnmount(() => {
  if (previousOverflow !== null) {
    document.body.style.overflow = previousOverflow
    previousOverflow = null
  }
})

function focusables(): HTMLElement[] {
  const root = rootRef.value
  if (!root) {
    return []
  }
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    )
  )
}

function onKeydown(event: KeyboardEvent): void {
  // 登录失败是强制告知态：Esc 不关闭，用户必须显式点按钮
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    return
  }
  if (event.key !== 'Tab') {
    return
  }
  const list = focusables()
  if (list.length === 0) {
    event.preventDefault()
    rootRef.value?.focus({ preventScroll: true })
    return
  }
  const first = list[0]
  const last = list[list.length - 1]
  const active = document.activeElement
  const inside = active instanceof HTMLElement && rootRef.value?.contains(active) === true
  if (event.shiftKey) {
    if (!inside || active === first) {
      event.preventDefault()
      last.focus({ preventScroll: true })
    }
  } else if (!inside || active === last) {
    event.preventDefault()
    first.focus({ preventScroll: true })
  }
}

function onAck(): void {
  emit('ack')
}

function onAction(): void {
  emit('action')
}
</script>

<template>
  <Teleport to="body">
    <div
      ref="rootRef"
      class="z-alert"
      :class="[`z-alert--${variant}`, { 'is-reduced': reducedMotion }]"
      :style="{ '--z-alert-accent': accentVar }"
      role="alertdialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="messageId"
      tabindex="-1"
      @keydown="onKeydown"
      @contextmenu.prevent
    >
      <div class="z-alert__scrim" aria-hidden="true" />
      <div class="z-alert__sweep" aria-hidden="true" />

      <div class="z-alert__stage">
        <div class="z-alert__card">
          <span class="z-alert__corner z-alert__corner--tl" aria-hidden="true" />
          <span class="z-alert__corner z-alert__corner--tr" aria-hidden="true" />
          <span class="z-alert__corner z-alert__corner--bl" aria-hidden="true" />
          <span class="z-alert__corner z-alert__corner--br" aria-hidden="true" />
          <span class="z-alert__seam" aria-hidden="true" />

          <p class="z-alert__head">
            <span class="z-alert__dot" aria-hidden="true" />
            <span class="z-alert__code">{{ decorCode }}</span>
          </p>

          <h2 :id="titleId" class="z-alert__title">{{ title }}</h2>
          <span class="z-alert__rule" aria-hidden="true" />
          <p :id="messageId" class="z-alert__message">{{ message }}</p>
          <p v-if="hint" class="z-alert__hint">{{ hint }}</p>

          <div v-if="showAck || showAction" class="z-alert__actions">
            <button
              v-if="showAck"
              ref="ackRef"
              type="button"
              class="z-alert__btn z-alert__btn--ghost"
              @click="onAck"
            >
              {{ ackText }}
            </button>
            <button
              v-if="showAction"
              ref="actionRef"
              type="button"
              class="z-alert__btn z-alert__btn--solid"
              @click="onAction"
            >
              {{ actionText }}
            </button>
          </div>

          <p class="z-alert__foot" aria-hidden="true">AUTH GATE · ZVMS</p>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 时间线：0ms 遮罩淡入 → 60ms 光带横扫 → 120ms 卡片沿中缝切开弹入 → 320ms 起
   装饰串/标题/正文/按钮逐级上浮 → 900ms 之后静止 */
.z-alert {
  --z-alert-accent: var(--yg-red-500);
  --z-alert-solid: var(--yg-color-danger-strong);
  --z-alert-accent-text: var(--yg-text-danger);
  --z-alert-on-solid: var(--yg-color-on-danger);
  --z-alert-surface: var(--yg-bg-surface-raised);
  --z-alert-fg: var(--yg-text-primary);
  --z-alert-fg-soft: var(--yg-text-secondary);
  --z-alert-fg-mute: var(--yg-text-tertiary);
  --z-alert-line: var(--yg-border-default);
  --z-alert-ease: cubic-bezier(0.19, 1, 0.22, 1);
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  padding: 24px;
  font-family: var(--yg-font-sans);
  color: var(--z-alert-fg);
  outline: none;
  overscroll-behavior: contain;
}

/* 琥珀（警示）变体：与红色高危同属一套语义，只是语气降一档 */
.z-alert--warning {
  --z-alert-solid: var(--yg-color-warning-strong);
  --z-alert-accent-text: var(--yg-text-warning);
  --z-alert-on-solid: var(--yg-color-on-warning);
}

.z-alert__scrim {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(
      ellipse 70% 55% at 50% 45%,
      color-mix(in srgb, var(--z-alert-accent) 24%, transparent) 0%,
      transparent 70%
    ),
    rgb(8 8 12 / 0.74);
  animation: z-alert-scrim-in 240ms ease-out both;
}

/* 网格纹理：静态，不逐帧重绘 */
.z-alert__scrim::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(to right, rgb(255 255 255 / 0.045) 1px, transparent 1px),
    linear-gradient(to bottom, rgb(255 255 255 / 0.045) 1px, transparent 1px);
  background-size: 48px 48px;
}

/* 光带横扫：一次性，跨过卡片（z-index 高于卡片）但压得足够低，不抢正文 */
.z-alert__sweep {
  position: absolute;
  z-index: 2;
  inset: -20%;
  pointer-events: none;
  background: linear-gradient(
    100deg,
    transparent 42%,
    color-mix(in srgb, var(--z-alert-accent) 22%, transparent) 48%,
    rgb(255 255 255 / 0.1) 50%,
    color-mix(in srgb, var(--z-alert-accent) 22%, transparent) 52%,
    transparent 58%
  );
  opacity: 0;
  transform: translate3d(-70%, 0, 0);
  will-change: transform, opacity;
  animation: z-alert-sweep 900ms cubic-bezier(0.16, 0, 0.3, 1) 60ms both;
}

.z-alert__stage {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 560px;
  display: flex;
}

.z-alert__card {
  position: relative;
  width: 100%;
  box-sizing: border-box;
  padding: 30px 28px 22px;
  overflow: hidden;
  background: var(--z-alert-surface);
  border: 1px solid var(--z-alert-line);
  box-shadow:
    0 24px 64px rgb(0 0 0 / 0.45),
    0 0 0 1px color-mix(in srgb, var(--z-alert-accent) 22%, transparent);
  will-change: transform, opacity, clip-path;
  animation: z-alert-card-in 460ms var(--z-alert-ease) 120ms both;
}

/* 四角 HUD 折角 */
.z-alert__corner {
  position: absolute;
  width: 18px;
  height: 18px;
  border: 2px solid var(--z-alert-accent);
  animation: z-alert-fade-in 300ms ease-out 240ms both;
}

.z-alert__corner--tl {
  top: -1px;
  left: -1px;
  border-right: 0;
  border-bottom: 0;
}

.z-alert__corner--tr {
  top: -1px;
  right: -1px;
  border-left: 0;
  border-bottom: 0;
}

.z-alert__corner--bl {
  bottom: -1px;
  left: -1px;
  border-right: 0;
  border-top: 0;
}

.z-alert__corner--br {
  bottom: -1px;
  right: -1px;
  border-left: 0;
  border-top: 0;
}

/* 中缝光线：卡片沿这条线被「切开」 */
.z-alert__seam {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 2px;
  margin-top: -1px;
  pointer-events: none;
  background: var(--z-alert-accent);
  box-shadow: 0 0 12px 2px color-mix(in srgb, var(--z-alert-accent) 70%, transparent);
  transform: scaleX(0);
  will-change: transform, opacity;
  animation: z-alert-seam 520ms var(--z-alert-ease) 120ms both;
}

.z-alert__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 14px;
  animation: z-alert-fade-up 260ms var(--z-alert-ease) 320ms both;
}

.z-alert__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--z-alert-accent);
  box-shadow: 0 0 10px var(--z-alert-accent);
  animation: z-alert-pulse 1.8s ease-in-out infinite;
}

.z-alert__code {
  font-family: var(--yg-font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: var(--z-alert-accent-text);
}

.z-alert__title {
  margin: 0;
  font-size: clamp(22px, 5vw, 30px);
  font-weight: 700;
  line-height: 1.25;
  color: var(--z-alert-fg);
  overflow-wrap: anywhere;
  animation: z-alert-fade-up 320ms var(--z-alert-ease) 380ms both;
}

.z-alert__rule {
  display: block;
  height: 2px;
  margin: 16px 0 18px;
  background: linear-gradient(
    90deg,
    var(--z-alert-accent),
    color-mix(in srgb, var(--z-alert-accent) 18%, transparent)
  );
  transform-origin: left center;
  animation: z-alert-rule 420ms var(--z-alert-ease) 460ms both;
}

.z-alert__message {
  margin: 0;
  font-size: 16px;
  line-height: 1.6;
  color: var(--z-alert-fg);
  overflow-wrap: anywhere;
  animation: z-alert-fade-up 320ms var(--z-alert-ease) 520ms both;
}

.z-alert__hint {
  margin: 14px 0 0;
  padding: 10px 12px;
  font-family: var(--yg-font-mono);
  font-size: 12px;
  line-height: 1.7;
  color: var(--z-alert-fg-soft);
  border-left: 2px solid var(--z-alert-accent);
  background: color-mix(in srgb, var(--z-alert-accent) 8%, transparent);
  overflow-wrap: anywhere;
  animation: z-alert-fade-up 320ms var(--z-alert-ease) 600ms both;
}

.z-alert__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
  animation: z-alert-fade-up 320ms var(--z-alert-ease) 680ms both;
}

.z-alert__btn {
  flex: 1 1 140px;
  min-height: 44px;
  padding: 10px 20px;
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-radius: 0;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    color 160ms ease,
    border-color 160ms ease,
    transform 120ms ease,
    box-shadow 160ms ease;
}

.z-alert__btn--ghost {
  background: transparent;
  color: var(--z-alert-fg);
  border: 1px solid var(--z-alert-line);
}

.z-alert__btn--ghost:hover {
  background: color-mix(in srgb, var(--z-alert-accent) 12%, transparent);
  border-color: var(--z-alert-accent);
}

.z-alert__btn--solid {
  background: var(--z-alert-solid);
  color: var(--z-alert-on-solid);
  border: 1px solid var(--z-alert-accent);
  box-shadow: 0 8px 24px color-mix(in srgb, var(--z-alert-accent) 35%, transparent);
}

.z-alert__btn--solid:hover {
  filter: brightness(1.08);
}

.z-alert__btn:active {
  transform: translateY(1px);
}

.z-alert__btn:focus-visible {
  outline: 2px solid var(--z-alert-accent);
  outline-offset: 3px;
}

.z-alert__foot {
  margin: 18px 0 0;
  font-family: var(--yg-font-mono);
  font-size: 11px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  color: var(--z-alert-fg-mute);
  animation: z-alert-fade-up 320ms var(--z-alert-ease) 760ms both;
}

@keyframes z-alert-scrim-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes z-alert-sweep {
  0% {
    opacity: 0;
    transform: translate3d(-70%, 0, 0);
  }
  12% {
    opacity: 1;
  }
  88% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: translate3d(70%, 0, 0);
  }
}

@keyframes z-alert-card-in {
  0% {
    opacity: 0;
    transform: translate3d(0, 18px, 0) scale(0.98);
    clip-path: inset(48% 0 48% 0);
  }
  100% {
    opacity: 1;
    transform: none;
    clip-path: inset(0 0 0 0);
  }
}

@keyframes z-alert-seam {
  0% {
    transform: scaleX(0);
    opacity: 1;
  }
  55% {
    transform: scaleX(1);
    opacity: 1;
  }
  100% {
    transform: scaleX(1);
    opacity: 0;
  }
}

@keyframes z-alert-rule {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

@keyframes z-alert-fade-up {
  from {
    opacity: 0;
    transform: translate3d(0, 12px, 0);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes z-alert-fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes z-alert-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.8);
  }
}

@media (max-width: 480px) {
  .z-alert {
    padding: 16px;
  }

  .z-alert__card {
    padding: 24px 18px 18px;
  }

  .z-alert__actions {
    flex-direction: column;
  }

  .z-alert__btn {
    width: 100%;
  }
}

/* 减少动效：装饰动画全停，保留静态可读画面 */
.z-alert.is-reduced *,
.z-alert.is-reduced *::before,
.z-alert.is-reduced *::after {
  animation: none !important;
  transition: none !important;
}

@media (prefers-reduced-motion: reduce) {
  .z-alert *,
  .z-alert *::before,
  .z-alert *::after {
    animation: none !important;
    transition: none !important;
  }
}

@media print {
  .z-alert {
    display: none !important;
  }
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    name: string
    role: string
    active?: boolean
  }>(),
  { active: true }
)

const emit = defineEmits<{ done: [] }>()

const { t } = useI18n()

interface Particle {
  style: Record<string, string>
}

interface Slice {
  style: Record<string, string>
}

const displayName = computed(() => props.name.trim() || t('login.reveal.fallbackName'))
const helloText = computed(() => t('login.reveal.hello', { name: displayName.value }))
const roleText = computed(() => t('login.reveal.role', { role: props.role }))

const helloLineRef = ref<HTMLElement | null>(null)
const roleLineRef = ref<HTMLElement | null>(null)
const particles = ref<Particle[]>([])
const slices = ref<Slice[]>([])

// 碎片预算（切片 + 粒子合计）：手机 ≤16、桌面 ≤32。
// 手机：4 片 × 2 行 = 8 切片 + 8 粒子 = 16；桌面：6 片 × 2 行 = 12 切片 + 20 粒子 = 32。
const DESKTOP_MIN_WIDTH = 768
const SLICES_PER_LINE = { mobile: 4, desktop: 6 } as const
const PARTICLE_COUNT = { mobile: 8, desktop: 20 } as const

// 触摸设备（平板/手机）强制使用 mobile 档位，减轻 P620 压力
const isTouchDevice =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(pointer: coarse)')?.matches === true

function isDesktop(): boolean {
  return !isTouchDevice && window.innerWidth >= DESKTOP_MIN_WIDTH
}

// 粒子只做 transform / opacity 动画。最晚结束时间 = 生成 2020ms + 延迟 80ms + 时长 650ms = 2750ms，
// 早于 2800ms 的 done，父级卸载时不会把还在飞的粒子硬切掉。
function spawnParticles(): void {
  const lines = [helloLineRef.value, roleLineRef.value].filter(
    (line): line is HTMLElement => line !== null
  )
  if (lines.length === 0) {
    return
  }

  // 一次性测量所有盒子位置，避免动画期间反复触发 layout
  const boxes = lines
    .map((line) => line.getBoundingClientRect())
    .filter((box) => box.width > 0 && box.height > 0)

  if (boxes.length === 0) {
    return
  }

  const count = isDesktop() ? PARTICLE_COUNT.desktop : PARTICLE_COUNT.mobile
  // 颜色交给主题令牌：浅色幕布上白粒子会消失，必须由 CSS 按主题换成深色碎屑。
  const palette = [
    'var(--reveal-particle-1)',
    'var(--reveal-particle-2)',
    'var(--reveal-particle-3)'
  ]
  const list: Particle[] = []

  for (let i = 0; i < count; i += 1) {
    const box = boxes[Math.floor(Math.random() * boxes.length)]
    const angle = Math.random() * Math.PI * 2
    const distance = 50 + Math.random() * 100
    const sliver = Math.random() < 0.3
    const size = 2 + Math.random() * 2.5
    const roll = Math.random()
    list.push({
      style: {
        left: `${(box.left + Math.random() * box.width).toFixed(1)}px`,
        top: `${(box.top + Math.random() * box.height).toFixed(1)}px`,
        width: `${(sliver ? 2 : size).toFixed(1)}px`,
        height: `${(sliver ? size + 5 + Math.random() * 4 : size).toFixed(1)}px`,
        background: roll < 0.6 ? palette[0] : roll < 0.88 ? palette[1] : palette[2],
        '--particle-x': `${(Math.cos(angle) * distance).toFixed(1)}px`,
        '--particle-y': `${(Math.sin(angle) * distance * 0.7 - 20).toFixed(1)}px`,
        '--particle-rotate': `${Math.round((Math.random() - 0.5) * 200)}deg`,
        '--particle-delay': `${Math.round(Math.random() * 80)}ms`,
        '--particle-duration': `${Math.round(500 + Math.random() * 150)}ms`
      }
    })
  }

  particles.value = list
}

// 生成几何切片：从文字行切出若干矩形片段，向不同方向飞散。
// 最晚结束时间 = 生成 2000ms + 延迟 (5×30+60)ms + 时长 380ms = 2590ms。
function spawnSlices(): void {
  const lines = [helloLineRef.value, roleLineRef.value].filter(
    (line): line is HTMLElement => line !== null
  )
  if (lines.length === 0) {
    return
  }

  const list: Slice[] = []
  const sliceCount = isDesktop() ? SLICES_PER_LINE.desktop : SLICES_PER_LINE.mobile

  lines.forEach((line) => {
    const box = line.getBoundingClientRect()
    if (box.width === 0 || box.height === 0) {
      return
    }

    const sliceWidth = box.width / sliceCount
    for (let i = 0; i < sliceCount; i += 1) {
      const angle = (Math.random() - 0.5) * Math.PI * 0.4
      const distance = 80 + Math.random() * 120
      const skewSign = Math.random() < 0.5 ? 1 : -1
      list.push({
        style: {
          left: `${(box.left + i * sliceWidth).toFixed(1)}px`,
          top: `${box.top.toFixed(1)}px`,
          width: `${sliceWidth.toFixed(1)}px`,
          height: `${box.height.toFixed(1)}px`,
          '--slice-x': `${(Math.cos(angle) * distance).toFixed(1)}px`,
          '--slice-y': `${(Math.sin(angle) * distance - 30).toFixed(1)}px`,
          '--slice-rotate': `${Math.round((Math.random() - 0.5) * 24)}deg`,
          '--slice-skew': `${(skewSign * (8 + Math.random() * 12)).toFixed(1)}deg`,
          '--slice-delay': `${Math.round(i * 30 + Math.random() * 60)}ms`
        }
      })
    }
  })

  slices.value = list
}

const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true

const TOTAL_MS = 2800
const REDUCED_MS = 1000
const SLICE_SPAWN_MS = 2000
const PARTICLE_SPAWN_MS = 2020
// 碎片动画最晚 2750ms 结束；清理只是兜底（父级没卸载时释放节点），放在全部动画之后。
const CLEANUP_MS = TOTAL_MS + 200

let doneTimer: number | undefined
let sliceTimer: number | undefined
let particleTimer: number | undefined
let cleanupTimer: number | undefined

const started = ref(false)

function startTimeline(): void {
  if (started.value) {
    return
  }
  started.value = true

  doneTimer = window.setTimeout(() => emit('done'), reducedMotion ? REDUCED_MS : TOTAL_MS)

  if (!reducedMotion) {
    sliceTimer = window.setTimeout(spawnSlices, SLICE_SPAWN_MS)
    particleTimer = window.setTimeout(spawnParticles, PARTICLE_SPAWN_MS)
    cleanupTimer = window.setTimeout(() => {
      particles.value = []
      slices.value = []
    }, CLEANUP_MS)
  }
}

watch(
  () => props.active,
  (v) => {
    if (v) {
      startTimeline()
    }
  }
)

onMounted(() => {
  if (props.active) {
    startTimeline()
  }
})

onBeforeUnmount(() => {
  if (doneTimer !== undefined) {
    window.clearTimeout(doneTimer)
  }
  if (sliceTimer !== undefined) {
    window.clearTimeout(sliceTimer)
  }
  if (particleTimer !== undefined) {
    window.clearTimeout(particleTimer)
  }
  if (cleanupTimer !== undefined) {
    window.clearTimeout(cleanupTimer)
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      class="login-reveal"
      :class="{ 'is-reduced': reducedMotion, 'is-waiting': !started }"
      role="status"
      aria-live="polite"
      :aria-label="`${helloText}，${roleText}`"
      @wheel.prevent
      @touchmove.prevent
    >
      <!-- 幕布：带网格纹理，2400ms 后拉开 -->
      <div class="login-reveal__panel login-reveal__panel--top" aria-hidden="true">
        <span class="login-reveal__rule"></span>
      </div>
      <div class="login-reveal__panel login-reveal__panel--bottom" aria-hidden="true">
        <span class="login-reveal__rule"></span>
      </div>

      <!-- 扫描线：0-400ms 从左上扫到右下 -->
      <div class="login-reveal__scanline" aria-hidden="true"></div>

      <div class="login-reveal__stage" aria-hidden="true">
        <!-- 品牌锁定：400-800ms ZVMS 字标几何组装，800-1200ms 光晕脉冲 -->
        <div class="login-reveal__brand">ZVMS</div>

        <!-- 用户信息：1200ms 淡入，2000ms 开始切片消散 -->
        <div class="login-reveal__user-info">
          <p ref="helloLineRef" class="login-reveal__line login-reveal__line--hello">
            {{ helloText }}
          </p>
          <p ref="roleLineRef" class="login-reveal__line login-reveal__line--role">
            {{ roleText }}
          </p>
        </div>
      </div>

      <!-- 几何切片：2000ms 生成，从文字位置向外飞散 -->
      <span
        v-for="(slice, index) in slices"
        :key="`slice-${index}`"
        class="login-reveal__slice"
        :style="slice.style"
        aria-hidden="true"
      ></span>

      <!-- 粒子：2020ms 生成，配合切片一起消散 -->
      <span
        v-for="(particle, index) in particles"
        :key="`particle-${index}`"
        class="login-reveal__particle"
        :style="particle.style"
        aria-hidden="true"
      ></span>
    </div>
  </Teleport>
</template>

<style scoped>
.login-reveal {
  --reveal-cyan: #49f4e2;
  --reveal-pink: #ff4e9b;
  --reveal-panel: #05080b;
  --reveal-particle-1: #f4f6f8;
  --reveal-particle-2: #49f4e2;
  --reveal-particle-3: #ff4e9b;
  --reveal-brand-glow: rgba(73, 244, 226, 0.8);
  position: fixed;
  inset: 0;
  z-index: 9500;
  overflow: hidden;
  background: transparent;
}

/* ========== 幕布 ========== */
.login-reveal__panel {
  position: absolute;
  left: 0;
  width: 100%;
  height: 50.2%;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(73, 244, 226, 0.05) 0 1px,
      transparent 1px 5px
    ),
    radial-gradient(100% 80% at 50% 50%, rgba(73, 244, 226, 0.08), transparent 68%),
    var(--reveal-panel);
  will-change: transform;
}

.login-reveal__panel--top {
  top: 0;
  animation: reveal-panel-up 400ms cubic-bezier(0.76, 0, 0.24, 1) 2400ms forwards;
}

.login-reveal__panel--bottom {
  bottom: 0;
  animation: reveal-panel-down 400ms cubic-bezier(0.76, 0, 0.24, 1) 2400ms forwards;
}

/* 幕布接缝发光线 */
.login-reveal__rule {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(
    90deg,
    rgba(73, 244, 226, 0) 0%,
    rgba(73, 244, 226, 0.4) 12%,
    #49f4e2 40%,
    #d8fffa 50%,
    #49f4e2 60%,
    rgba(73, 244, 226, 0.4) 88%,
    rgba(73, 244, 226, 0) 100%
  );
  box-shadow:
    0 0 12px rgba(73, 244, 226, 0.7),
    0 0 32px rgba(73, 244, 226, 0.35);
  transform: scaleX(0);
  transform-origin: 50% 50%;
  animation: reveal-rule-grow 360ms cubic-bezier(0.19, 1, 0.22, 1) 2040ms forwards;
}

.login-reveal__panel--top .login-reveal__rule {
  bottom: -1px;
}

.login-reveal__panel--bottom .login-reveal__rule {
  top: -1px;
}

/* ========== 扫描线 ========== */
.login-reveal__scanline {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
  background: linear-gradient(
    135deg,
    transparent 0%,
    transparent 48%,
    rgba(73, 244, 226, 0.15) 49%,
    rgba(73, 244, 226, 0.6) 50%,
    rgba(255, 255, 255, 0.8) 50.5%,
    rgba(73, 244, 226, 0.6) 51%,
    rgba(73, 244, 226, 0.15) 52%,
    transparent 53%,
    transparent 100%
  );
  opacity: 0;
  transform: translate3d(-120%, -120%, 0);
  will-change: transform, opacity;
  animation: reveal-scanline-sweep 400ms cubic-bezier(0.16, 0, 0.3, 1) 0ms forwards;
}

@keyframes reveal-scanline-sweep {
  0% {
    opacity: 0;
    transform: translate3d(-120%, -120%, 0);
  }
  10% {
    opacity: 1;
  }
  90% {
    opacity: 0.9;
  }
  100% {
    opacity: 0;
    transform: translate3d(120%, 120%, 0);
  }
}

/* ========== 舞台 ========== */
.login-reveal__stage {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 24px;
  text-align: center;
  pointer-events: none;
}

/* ========== 品牌锁定 ========== */
.login-reveal__brand {
  color: #f4f6f8;
  font-family: var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  font-size: clamp(64px, 12vw, 120px);
  font-weight: 900;
  letter-spacing: 0.18em;
  line-height: 1;
  text-shadow:
    3px 0 rgba(255, 78, 155, 0.6),
    -3px 0 rgba(73, 244, 226, 0.7);
  opacity: 0;
  clip-path: inset(50% 50% 50% 50%);
  will-change: clip-path, opacity, filter;
  animation:
    reveal-brand-assemble 400ms cubic-bezier(0.19, 1, 0.22, 1) 400ms forwards,
    reveal-brand-out 200ms ease 1200ms forwards;
}

/* 触摸设备保留组装和淡出，跳过脉冲以减轻 GPU 负担 */
@media (pointer: fine) {
  .login-reveal__brand {
    animation:
      reveal-brand-assemble 400ms cubic-bezier(0.19, 1, 0.22, 1) 400ms forwards,
      reveal-brand-pulse 400ms cubic-bezier(0.33, 0, 0.67, 1) 800ms forwards,
      reveal-brand-out 200ms ease 1200ms forwards;
  }
}

/* 品牌组装使用 inset 从中心向外展开（四边收缩至中心点，再打开） */
@keyframes reveal-brand-assemble {
  0% {
    opacity: 0;
    clip-path: inset(50% 50% 50% 50%);
  }
  100% {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

@keyframes reveal-brand-pulse {
  0% {
    filter: drop-shadow(0 0 0 transparent);
  }
  50% {
    filter: drop-shadow(0 0 32px var(--reveal-brand-glow));
  }
  100% {
    filter: drop-shadow(0 0 0 transparent);
  }
}

@keyframes reveal-brand-out {
  to {
    opacity: 0;
    transform: translateY(-8px);
  }
}

/* ========== 用户信息 ========== */
.login-reveal__user-info {
  opacity: 0;
  transform: translateY(12px);
  clip-path: inset(0 0 0 0);
  animation: reveal-user-in 300ms cubic-bezier(0.16, 1, 0.3, 1) 1200ms forwards,
    reveal-user-out 400ms cubic-bezier(0.4, 0, 0.6, 1) 2000ms forwards;
}

@keyframes reveal-user-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 2000ms 文字消散，便宜操作（opacity + clip-path 横向擦除），让切片接住视觉。 */
@keyframes reveal-user-out {
  to {
    opacity: 0;
    clip-path: inset(0 100% 0 0);
  }
}

.login-reveal__line {
  margin: 0;
  color: #f4f6f8;
  line-height: 1.3;
  white-space: nowrap;
  text-shadow:
    2px 0 rgba(255, 78, 155, 0.5),
    -2px 0 rgba(73, 244, 226, 0.55);
}

.login-reveal__line--hello {
  margin-bottom: 8px;
  font-size: clamp(32px, 5.6vw, 64px);
  font-weight: 700;
  letter-spacing: 0.03em;
  text-shadow:
    3px 0 rgba(255, 78, 155, 0.55),
    -3px 0 rgba(73, 244, 226, 0.65);
}

.login-reveal__line--role {
  color: rgba(244, 246, 248, 0.75);
  font-size: clamp(16px, 2.2vw, 28px);
  font-weight: 600;
  letter-spacing: 0.12em;
}

/* ========== 几何切片 ========== */
.login-reveal__slice {
  position: absolute;
  z-index: 4;
  pointer-events: none;
  background: linear-gradient(
    135deg,
    rgba(73, 244, 226, 0.15),
    rgba(73, 244, 226, 0.08) 50%,
    rgba(255, 78, 155, 0.12)
  );
  border: 1px solid rgba(73, 244, 226, 0.3);
  opacity: 0;
  will-change: transform, opacity;
  animation: reveal-slice-out 380ms cubic-bezier(0.4, 0, 0.8, 0.2) var(--slice-delay, 0ms) forwards;
}

@keyframes reveal-slice-out {
  0% {
    opacity: 0;
    transform: translate3d(0, 0, 0) rotate(0deg) skewX(0deg);
  }
  10% {
    opacity: 0.8;
  }
  100% {
    opacity: 0;
    transform: translate3d(var(--slice-x), var(--slice-y), 0)
      rotate(var(--slice-rotate, 0deg)) skewX(var(--slice-skew, 0deg));
  }
}

/* ========== 粒子 ========== */
.login-reveal__particle {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  opacity: 0;
  will-change: transform, opacity;
  animation: reveal-particle-out var(--particle-duration, 700ms)
    cubic-bezier(0.16, 0.84, 0.32, 1) var(--particle-delay, 0ms) forwards;
}

@keyframes reveal-particle-out {
  0% {
    opacity: 0;
    transform: translate3d(0, 0, 0) scale(0.5);
  }
  12% {
    opacity: 0.9;
  }
  60% {
    opacity: 0.85;
  }
  100% {
    opacity: 0;
    transform: translate3d(var(--particle-x), var(--particle-y), 0)
      rotate(var(--particle-rotate, 0deg)) scale(0.2);
  }
}

/* ========== 面板动画 ========== */
@keyframes reveal-rule-grow {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

@keyframes reveal-panel-up {
  from {
    transform: translate3d(0, 0, 0);
  }
  to {
    transform: translate3d(0, -100%, 0);
  }
}

@keyframes reveal-panel-down {
  from {
    transform: translate3d(0, 0, 0);
  }
  to {
    transform: translate3d(0, 100%, 0);
  }
}

/* ========== 浅色主题 ========== */
/* 浅色下不再使用黑幕：幕布换成冷白/浅青纸面，粒子换成深色碎屑，
   否则原来的白色粒子与霓虹描边在浅底上几乎不可见。 */
html[data-theme='light'] .login-reveal {
  --reveal-cyan: #0b9f93;
  --reveal-pink: #d93672;
  --reveal-panel: #f4f8f9;
  --reveal-particle-1: #0b0e12;
  --reveal-particle-2: #0b9f93;
  --reveal-particle-3: #d93672;
  --reveal-brand-glow: rgba(11, 159, 147, 0.45);
}

html[data-theme='light'] .login-reveal__panel {
  background:
    repeating-linear-gradient(
      0deg,
      rgba(11, 159, 147, 0.06) 0 1px,
      transparent 1px 5px
    ),
    radial-gradient(100% 80% at 50% 50%, rgba(11, 159, 147, 0.12), transparent 68%),
    linear-gradient(135deg, #ffffff 0%, #eef4f5 100%);
}

html[data-theme='light'] .login-reveal__rule {
  background: linear-gradient(
    90deg,
    rgba(11, 159, 147, 0) 0%,
    rgba(11, 159, 147, 0.35) 12%,
    #0b9f93 40%,
    #ffffff 50%,
    #0b9f93 60%,
    rgba(11, 159, 147, 0.35) 88%,
    rgba(11, 159, 147, 0) 100%
  );
  box-shadow:
    0 0 12px rgba(11, 159, 147, 0.45),
    0 0 32px rgba(11, 159, 147, 0.22);
}

html[data-theme='light'] .login-reveal__scanline {
  background: linear-gradient(
    135deg,
    transparent 0%,
    transparent 48%,
    rgba(11, 159, 147, 0.12) 49%,
    rgba(11, 159, 147, 0.55) 50%,
    rgba(5, 6, 8, 0.34) 50.5%,
    rgba(11, 159, 147, 0.55) 51%,
    rgba(11, 159, 147, 0.12) 52%,
    transparent 53%,
    transparent 100%
  );
}

html[data-theme='light'] .login-reveal__brand {
  color: #0b0e12;
  text-shadow:
    3px 0 rgba(217, 54, 114, 0.16),
    -3px 0 rgba(11, 159, 147, 0.18);
}

html[data-theme='light'] .login-reveal__line {
  color: #0b0e12;
  text-shadow:
    2px 0 rgba(217, 54, 114, 0.12),
    -2px 0 rgba(11, 159, 147, 0.16);
}

html[data-theme='light'] .login-reveal__line--hello {
  text-shadow:
    3px 0 rgba(217, 54, 114, 0.14),
    -3px 0 rgba(11, 159, 147, 0.18);
}

html[data-theme='light'] .login-reveal__line--role {
  color: rgba(11, 14, 18, 0.62);
}

html[data-theme='light'] .login-reveal__slice {
  background: linear-gradient(
    135deg,
    rgba(11, 159, 147, 0.12),
    rgba(11, 159, 147, 0.06) 50%,
    rgba(217, 54, 114, 0.1)
  );
  border-color: rgba(11, 159, 147, 0.32);
}

/* ========== Reduced Motion ========== */
.login-reveal.is-reduced {
  /* 保持不透明覆盖，防止 nav 闪现 */
  background: var(--reveal-panel);
  animation: reveal-fade-out 300ms ease 700ms forwards;
}

.login-reveal.is-reduced .login-reveal__panel,
.login-reveal.is-reduced .login-reveal__rule,
.login-reveal.is-reduced .login-reveal__scanline,
.login-reveal.is-reduced .login-reveal__brand {
  animation: none;
  opacity: 0;
}

.login-reveal.is-reduced .login-reveal__user-info {
  animation: none;
  opacity: 1;
  transform: translateY(0);
}

@keyframes reveal-fade-out {
  to {
    opacity: 0;
  }
}

/* ========== Waiting State ========== */
/* 等待态：暂停所有动画（含 reduced 下根节点的淡出），移除合成层，保持不透明幕布 */
.login-reveal.is-waiting,
.login-reveal.is-waiting * {
  animation-play-state: paused !important;
}

.login-reveal.is-waiting .login-reveal__panel,
.login-reveal.is-waiting .login-reveal__scanline,
.login-reveal.is-waiting .login-reveal__brand,
.login-reveal.is-waiting .login-reveal__slice,
.login-reveal.is-waiting .login-reveal__particle {
  will-change: auto;
}
</style>

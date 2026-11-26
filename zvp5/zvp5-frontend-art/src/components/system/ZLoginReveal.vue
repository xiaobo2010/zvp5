<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'

const props = withDefaults(
  defineProps<{
    name: string
    role: string
    active?: boolean
    /**
     * 预热模式：登录页空闲时先挂一个不可见实例，把样式解析、六边形底纹
     * 的 SVG 路径和合成层创建提前付掉；不启动时间线、不读屏、不抢焦点。
     */
    prewarm?: boolean
  }>(),
  { active: true, prewarm: false }
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
const userInfoRef = ref<HTMLElement | null>(null)
// 预先算好碎片的起始位置/随机向量，2000ms 时只切换 is-live 类，不再测量布局。
const cachedParticles = ref<Particle[]>([])
const cachedSlices = ref<Slice[]>([])
const slicesLive = ref(false)
const particlesLive = ref(false)
const dissolveCached = ref(false)

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
function buildParticles(boxes: DOMRect[]): Particle[] {
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

  return list
}

// 生成几何切片：从文字行切出若干矩形片段，向不同方向飞散。
// 最晚结束时间 = 生成 2000ms + 延迟 (5×30+60)ms + 时长 380ms = 2590ms。
function buildSlices(boxes: DOMRect[]): Slice[] {
  const list: Slice[] = []
  const sliceCount = isDesktop() ? SLICES_PER_LINE.desktop : SLICES_PER_LINE.mobile

  boxes.forEach((box) => {
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

  return list
}

/**
 * 在挂载后（幕布等待期）一次性测量文字盒子并预生成碎片。
 *
 * 原来是在 2000ms 处现场 getBoundingClientRect() + 随机生成 32 个节点，
 * 正好和文字消散同一帧，低端平板会掉帧。现在把这一笔成本移到等待期；
 * 真正消散时只切换 is-live 类，DOM 节点已经存在，动画由合成器接管。
 *
 * 测量时临时去掉 user-info 的 translateY(12px) 入场位移，否则缓存的
 * 起始点会比最终文字位置低 12px。
 */
function cacheDissolve(): void {
  if (reducedMotion || dissolveCached.value) {
    return
  }

  const lines = [helloLineRef.value, roleLineRef.value].filter(
    (line): line is HTMLElement => line !== null
  )
  if (lines.length === 0) {
    return
  }

  const info = userInfoRef.value
  const previousTransform = info?.style.transform ?? ''
  const previousOpacity = info?.style.opacity ?? ''
  if (info) {
    info.style.transform = 'none'
    info.style.opacity = '1'
  }

  const boxes = lines
    .map((line) => line.getBoundingClientRect())
    .filter((box) => box.width > 0 && box.height > 0)

  if (info) {
    info.style.transform = previousTransform
    info.style.opacity = previousOpacity
  }

  if (boxes.length === 0) {
    return
  }

  cachedParticles.value = buildParticles(boxes)
  cachedSlices.value = buildSlices(boxes)
  dissolveCached.value = true
}

// 切片/粒子节点在等待期就渲染好；这里只负责到点放行动画。
function spawnSlices(): void {
  if (!dissolveCached.value) {
    cacheDissolve()
  }
  slicesLive.value = true
}

function spawnParticles(): void {
  if (!dissolveCached.value) {
    cacheDissolve()
  }
  particlesLive.value = true
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
  if (started.value || props.prewarm) {
    return
  }
  started.value = true

  doneTimer = window.setTimeout(() => emit('done'), reducedMotion ? REDUCED_MS : TOTAL_MS)

  if (!reducedMotion) {
    sliceTimer = window.setTimeout(spawnSlices, SLICE_SPAWN_MS)
    particleTimer = window.setTimeout(spawnParticles, PARTICLE_SPAWN_MS)
    cleanupTimer = window.setTimeout(() => {
      cachedParticles.value = []
      cachedSlices.value = []
      slicesLive.value = false
      particlesLive.value = false
    }, CLEANUP_MS)
  }
}

watch(
  () => props.active,
  (v) => {
    if (v && !props.prewarm) {
      startTimeline()
    }
  }
)

onMounted(() => {
  if (props.prewarm) {
    return
  }
  // 等待期/首帧后就把文字盒子和随机碎片算好，避免 2000ms 与文字消散抢同一帧。
  nextTick(() => cacheDissolve())
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
  cachedParticles.value = []
  cachedSlices.value = []
})
</script>

<template>
  <Teleport to="body">
    <div
      class="login-reveal"
      :class="{ 'is-reduced': reducedMotion, 'is-waiting': !started, 'is-prewarm': prewarm }"
      :role="prewarm ? undefined : 'status'"
      :aria-live="prewarm ? undefined : 'polite'"
      :aria-label="prewarm ? undefined : `${helloText}，${roleText}`"
      :aria-hidden="prewarm || undefined"
      :inert="prewarm || undefined"
      @wheel.prevent
      @touchmove.prevent
    >
      <!-- 氛围层：NERV 蜂窝/准星/危险条 + Phigros 判定线 -->
      <div class="login-reveal__atmosphere" aria-hidden="true">
        <ZHexGrid
          :size="64"
          color="var(--reveal-cyan)"
          :opacity="0.07"
          drift
          flicker
        />
        <div class="login-reveal__reticle"></div>
        <div class="login-reveal__judgment"></div>
        <div class="login-reveal__hazard login-reveal__hazard--top"></div>
        <div class="login-reveal__hazard login-reveal__hazard--bottom"></div>
      </div>

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
        <div class="login-reveal__hud login-reveal__hud--top">
          <span>SYS // AUTH</span>
          <span>SECURE // CHANNEL 01</span>
        </div>
        <div class="login-reveal__hud login-reveal__hud--bottom">
          <span>PHI // 1.0</span>
          <span>AUTH SEQUENCE // COMPLETE</span>
        </div>

        <!-- 品牌锁定：400-800ms ZVMS 字标几何组装，800-1200ms 光晕脉冲 -->
        <div class="login-reveal__brand">ZVMS</div>

        <!-- 用户信息：1200ms 淡入，2000ms 开始切片消散 -->
        <div ref="userInfoRef" class="login-reveal__user-info">
          <p ref="helloLineRef" class="login-reveal__line login-reveal__line--hello">
            {{ helloText }}
          </p>
          <p ref="roleLineRef" class="login-reveal__line login-reveal__line--role">
            {{ roleText }}
          </p>
        </div>
      </div>

      <!-- 认证进度：和 2400ms 的幕布开启对齐 -->
      <div class="login-reveal__progress" aria-hidden="true">
        <span class="login-reveal__progress-label">AUTH // SEQUENCE</span>
        <span class="login-reveal__progress-track">
          <span class="login-reveal__progress-fill"></span>
        </span>
      </div>

      <!-- 几何切片：等待期已预生成，2000ms 只切换 is-live 放行动画 -->
      <span
        v-for="(slice, index) in cachedSlices"
        :key="`slice-${index}`"
        class="login-reveal__slice"
        :class="{ 'is-live': slicesLive }"
        :style="slice.style"
        aria-hidden="true"
      ></span>

      <!-- 粒子：2020ms 配合切片一起消散 -->
      <span
        v-for="(particle, index) in cachedParticles"
        :key="`particle-${index}`"
        class="login-reveal__particle"
        :class="{ 'is-live': particlesLive }"
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
  --reveal-warn: #ff7a3c;
  --reveal-panel: #05080b;
  --reveal-particle-1: #f4f6f8;
  --reveal-particle-2: #49f4e2;
  --reveal-particle-3: #ff4e9b;
  --reveal-brand-glow: rgba(73, 244, 226, 0.8);
  --reveal-glitch-a: rgba(255, 78, 155, 0.78);
  --reveal-glitch-b: rgba(73, 244, 226, 0.88);
  --reveal-hud: rgba(244, 246, 248, 0.56);
  --reveal-track: rgba(73, 244, 226, 0.16);
  position: fixed;
  inset: 0;
  z-index: 9500;
  overflow: hidden;
  background: transparent;
}

/* 预热实例：不可见但保留完整绘制/合成路径，登录页空闲时先付掉首帧成本 */
.login-reveal.is-prewarm {
  opacity: 0;
  pointer-events: none;
}

/* ========== 氛围层 ========== */
.login-reveal__atmosphere {
  position: absolute;
  inset: 0;
  z-index: 1;
  overflow: hidden;
  pointer-events: none;
  animation: reveal-atmosphere-out 360ms ease 2400ms forwards;
}

/* 幕布开启时氛围层同步淡出，避免六边形/危险条盖在首页上 */
@keyframes reveal-atmosphere-out {
  to {
    opacity: 0;
  }
}

/* NERV 准星环：只做慢速旋转，和 stage 的文字保持距离 */
.login-reveal__reticle {
  position: absolute;
  top: 50%;
  left: 50%;
  width: min(58vw, 560px);
  aspect-ratio: 1;
  border: 1px dashed var(--reveal-cyan);
  border-radius: 50%;
  opacity: 0.2;
  transform: translate3d(-50%, -50%, 0);
  animation: reveal-reticle-spin 32s linear infinite;
  will-change: transform;
}

.login-reveal__reticle::before,
.login-reveal__reticle::after {
  content: '';
  position: absolute;
  background: var(--reveal-cyan);
  opacity: 0.16;
}

.login-reveal__reticle::before {
  top: 50%;
  left: 7%;
  right: 7%;
  height: 1px;
}

.login-reveal__reticle::after {
  top: 7%;
  bottom: 7%;
  left: 50%;
  width: 1px;
}

@keyframes reveal-reticle-spin {
  to {
    transform: translate3d(-50%, -50%, 0) rotate(360deg);
  }
}

/* Phigros 判定线：900ms 展开，2000ms 文字消散时闪一次 */
.login-reveal__judgment {
  position: absolute;
  top: 50%;
  left: 7%;
  right: 7%;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--reveal-cyan) 22%,
    var(--reveal-pink) 78%,
    transparent 100%
  );
  opacity: 0.42;
  transform: scaleX(0);
  transform-origin: 50% 50%;
  animation:
    reveal-judgment-grow 600ms cubic-bezier(0.19, 1, 0.22, 1) 900ms forwards,
    reveal-judgment-flash 360ms ease 2000ms forwards;
  will-change: transform, opacity;
}

.login-reveal__judgment::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 8px;
  height: 8px;
  background: var(--reveal-cyan);
  transform: translate(-50%, -50%) rotate(45deg);
  box-shadow: 0 0 14px var(--reveal-cyan);
}

@keyframes reveal-judgment-grow {
  to {
    transform: scaleX(1);
  }
}

@keyframes reveal-judgment-flash {
  0%,
  100% {
    opacity: 0.42;
  }
  18% {
    opacity: 1;
    filter: drop-shadow(0 0 10px var(--reveal-cyan));
  }
}

/* NERV 危险条：斜纹沿 45° 平移一个周期，只动 transform */
.login-reveal__hazard {
  position: absolute;
  left: 0;
  right: 0;
  height: 10px;
  overflow: hidden;
  opacity: 0.22;
}

.login-reveal__hazard::before {
  content: '';
  position: absolute;
  inset: 0 -48px;
  background: repeating-linear-gradient(
    -45deg,
    var(--reveal-cyan) 0 10px,
    transparent 10px 20px
  );
  animation: reveal-hazard-crawl 3.8s linear infinite;
  will-change: transform;
}

.login-reveal__hazard--top {
  top: 0;
}

.login-reveal__hazard--bottom {
  bottom: 0;
  transform: scaleY(-1);
}

@keyframes reveal-hazard-crawl {
  to {
    transform: translate3d(-14.14px, 14.14px, 0);
  }
}

/* ========== 幕布 ========== */
.login-reveal__panel {
  position: absolute;
  left: 0;
  z-index: 0;
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
  z-index: 2;
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
  z-index: 3;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 24px;
  text-align: center;
  pointer-events: none;
}

/* NERV 读数：顶部/底部各一条，360ms 淡入，2120ms 随文字一起退场 */
.login-reveal__hud {
  position: absolute;
  left: clamp(18px, 4vw, 48px);
  right: clamp(18px, 4vw, 48px);
  display: flex;
  justify-content: space-between;
  color: var(--reveal-hud);
  font: 500 10px/1 var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  letter-spacing: 0.16em;
  text-transform: uppercase;
  opacity: 0;
  animation:
    reveal-hud-in 280ms ease 360ms forwards,
    reveal-hud-out 220ms ease 2120ms forwards;
}

.login-reveal__hud--top {
  top: clamp(18px, 4vh, 40px);
}

.login-reveal__hud--bottom {
  bottom: clamp(56px, 8vh, 76px);
}

.login-reveal__hud span {
  display: inline-flex;
  align-items: center;
}

.login-reveal__hud span::before {
  content: '';
  width: 5px;
  height: 5px;
  margin-right: 6px;
  background: var(--reveal-warn);
  transform: rotate(45deg);
  box-shadow: 0 0 8px var(--reveal-warn);
}

.login-reveal__hud span:last-child::before {
  background: var(--reveal-cyan);
  box-shadow: 0 0 8px var(--reveal-cyan);
}

@keyframes reveal-hud-in {
  to {
    opacity: 1;
  }
}

@keyframes reveal-hud-out {
  to {
    opacity: 0;
  }
}

/* 认证进度：2400ms 刚好走满，幕布同时开启 */
.login-reveal__progress {
  position: absolute;
  z-index: 3;
  bottom: clamp(20px, 4vh, 36px);
  left: 50%;
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(360px, 62vw);
  color: var(--reveal-hud);
  font: 500 9px/1 var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  letter-spacing: 0.14em;
  transform: translateX(-50%);
  animation: reveal-progress-out 240ms ease 2400ms forwards;
}

@keyframes reveal-progress-out {
  to {
    opacity: 0;
  }
}

.login-reveal__progress-label {
  white-space: nowrap;
}

.login-reveal__progress-track {
  position: relative;
  flex: 1;
  height: 2px;
  overflow: hidden;
  background: var(--reveal-track);
}

.login-reveal__progress-fill {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, var(--reveal-cyan), var(--reveal-pink));
  box-shadow: 0 0 8px var(--reveal-cyan);
  transform: scaleX(0);
  transform-origin: 0 50%;
  animation: reveal-progress-fill 2400ms linear forwards;
}

@keyframes reveal-progress-fill {
  to {
    transform: scaleX(1);
  }
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
    3px 0 var(--reveal-glitch-a),
    -3px 0 var(--reveal-glitch-b);
  opacity: 0;
  clip-path: inset(50% 50% 50% 50%);
  will-change: clip-path, opacity, filter;
  animation:
    reveal-brand-assemble 400ms cubic-bezier(0.19, 1, 0.22, 1) 400ms forwards,
    reveal-brand-glitch 900ms steps(1, end) 520ms 1,
    reveal-brand-out 200ms ease 1200ms forwards;
}

/* 触摸设备保留组装和淡出，跳过脉冲以减轻 GPU 负担 */
@media (pointer: fine) {
  .login-reveal__brand {
    animation:
      reveal-brand-assemble 400ms cubic-bezier(0.19, 1, 0.22, 1) 400ms forwards,
      reveal-brand-glitch 900ms steps(1, end) 520ms 1,
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

/* 稀疏故障：只改 text-shadow，不和组装/退场的 transform 抢属性 */
@keyframes reveal-brand-glitch {
  0%,
  66%,
  72%,
  100% {
    text-shadow:
      3px 0 var(--reveal-glitch-a),
      -3px 0 var(--reveal-glitch-b);
  }
  67% {
    text-shadow:
      -7px 0 var(--reveal-glitch-a),
      7px 0 var(--reveal-glitch-b),
      0 0 24px var(--reveal-glitch-b);
  }
  68% {
    text-shadow:
      5px 0 var(--reveal-glitch-a),
      -5px 0 var(--reveal-glitch-b);
  }
  69% {
    text-shadow:
      -2px 0 var(--reveal-glitch-a),
      2px 0 var(--reveal-glitch-b);
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
    2px 0 var(--reveal-glitch-a),
    -2px 0 var(--reveal-glitch-b);
}

.login-reveal__line--hello {
  margin-bottom: 8px;
  font-size: clamp(32px, 5.6vw, 64px);
  font-weight: 700;
  letter-spacing: 0.03em;
  text-shadow:
    3px 0 var(--reveal-glitch-a),
    -3px 0 var(--reveal-glitch-b);
  animation: reveal-glitch-name 800ms steps(1, end) 1450ms 1;
}

/* 用户名在 1450ms 短暂错位，正好落在 1200-2000ms 的可读窗口内 */
@keyframes reveal-glitch-name {
  0%,
  55%,
  62%,
  100% {
    text-shadow:
      3px 0 var(--reveal-glitch-a),
      -3px 0 var(--reveal-glitch-b);
  }
  56% {
    text-shadow:
      -5px 0 var(--reveal-glitch-a),
      5px 0 var(--reveal-glitch-b);
  }
  57% {
    text-shadow:
      4px 0 var(--reveal-glitch-a),
      -4px 0 var(--reveal-glitch-b);
  }
  58% {
    text-shadow:
      -2px 0 var(--reveal-glitch-a),
      2px 0 var(--reveal-glitch-b);
  }
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
  z-index: 5;
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
}

.login-reveal__slice.is-live {
  animation: reveal-slice-out 380ms cubic-bezier(0.4, 0, 0.8, 0.2) var(--slice-delay, 0ms)
    forwards;
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
  z-index: 4;
  pointer-events: none;
  opacity: 0;
  will-change: transform, opacity;
}

.login-reveal__particle.is-live {
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
  --reveal-warn: #b45309;
  --reveal-panel: #f4f8f9;
  --reveal-particle-1: #0b0e12;
  --reveal-particle-2: #0b9f93;
  --reveal-particle-3: #d93672;
  --reveal-brand-glow: rgba(11, 159, 147, 0.45);
  --reveal-glitch-a: rgba(217, 54, 114, 0.55);
  --reveal-glitch-b: rgba(11, 159, 147, 0.65);
  --reveal-hud: rgba(5, 6, 8, 0.58);
  --reveal-track: rgba(11, 159, 147, 0.18);
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
    3px 0 var(--reveal-glitch-a),
    -3px 0 var(--reveal-glitch-b);
}

html[data-theme='light'] .login-reveal__line {
  color: #0b0e12;
  text-shadow:
    2px 0 var(--reveal-glitch-a),
    -2px 0 var(--reveal-glitch-b);
}

html[data-theme='light'] .login-reveal__line--hello {
  text-shadow:
    3px 0 var(--reveal-glitch-a),
    -3px 0 var(--reveal-glitch-b);
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

.login-reveal.is-reduced .login-reveal__line--hello {
  animation: none;
}

/* 减少动效时氛围层没有信息价值，直接移出渲染树，避免静态用户白付合成成本 */
.login-reveal.is-reduced .login-reveal__atmosphere,
.login-reveal.is-reduced .login-reveal__hud,
.login-reveal.is-reduced .login-reveal__progress {
  display: none;
}

@keyframes reveal-fade-out {
  to {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .login-reveal__atmosphere,
  .login-reveal__hud,
  .login-reveal__progress {
    display: none;
  }

  .login-reveal__line--hello {
    animation: none;
  }
}

/* ========== Waiting State ========== */
/* 等待态：暂停所有动画（含 reduced 下根节点的淡出），移除合成层，保持不透明幕布 */
.login-reveal.is-waiting,
.login-reveal.is-waiting * {
  animation-play-state: paused !important;
}

/* 子组件内部的蜂窝漂移不受 scoped 的 `*` 影响，单独用 :deep 暂停 */
.login-reveal.is-waiting :deep(.z-hex-grid__layer),
.login-reveal.is-waiting :deep(.z-hex-grid__svg) {
  animation-play-state: paused !important;
}

.login-reveal.is-waiting .login-reveal__panel,
.login-reveal.is-waiting .login-reveal__scanline,
.login-reveal.is-waiting .login-reveal__brand {
  will-change: auto;
}
</style>

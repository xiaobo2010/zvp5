<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  name: string
  role: string
}>()

const emit = defineEmits<{ done: [] }>()

interface Glyph {
  char: string
  accent: boolean
  style: Record<string, string>
}

interface Particle {
  style: Record<string, string>
}

/**
 * 每个字的模糊/熄灭时间略微错开，整行看起来是一层层糊掉，
 * 而不是同一帧整体消失。随机量只算一次，重渲染不会跳。
 */
function glyphsOf(text: string, accentFrom: number): Glyph[] {
  return Array.from(text).map((char, index) => ({
    char,
    accent: index >= accentFrom,
    style: {
      '--reveal-glyph-delay': `${Math.round(index * 16 + Math.random() * 90)}ms`
    }
  }))
}

const displayName = computed(() => props.name.trim() || '同学')
const helloText = computed(() => `你好，${displayName.value}`)
const roleText = computed(() => `身份：${props.role}`)

const helloGlyphs = computed(() => glyphsOf(helloText.value, 3))
const roleGlyphs = computed(() => glyphsOf(roleText.value, 3))

const helloLineRef = ref<HTMLElement | null>(null)
const roleLineRef = ref<HTMLElement | null>(null)
const particles = ref<Particle[]>([])

// 粒子数量按屏宽收敛：手机 24 颗、桌面 44 颗。全部只做 transform / opacity 动画，
// 不挂模糊，避免低端机在故障段掉帧。
function spawnParticles(): void {
  const lines = [helloLineRef.value, roleLineRef.value].filter(
    (line): line is HTMLElement => line !== null
  )
  const pools = lines
    .map((line) => {
      const glyphs = Array.from(line.querySelectorAll<HTMLElement>('.login-reveal__glyph'))
      return (glyphs.length > 0 ? glyphs : [line])
        .map((el) => el.getBoundingClientRect())
        .filter((box) => box.width > 0 && box.height > 0)
    })
    .filter((boxes) => boxes.length > 0)
  if (pools.length === 0) {
    return
  }

  const count = window.innerWidth >= 768 ? 44 : 24
  const palette = ['#f4f6f8', '#49f4e2', '#ff4e9b']
  const list: Particle[] = []

  for (let i = 0; i < count; i += 1) {
    const pool = pools[Math.floor(Math.random() * pools.length)]
    const box = pool[Math.floor(Math.random() * pool.length)]
    const angle = Math.random() * Math.PI * 2
    const distance = 40 + Math.random() * 116
    const sliver = Math.random() < 0.22
    const size = 2 + Math.random() * 2
    const roll = Math.random()
    list.push({
      style: {
        left: `${(box.left + Math.random() * box.width).toFixed(1)}px`,
        top: `${(box.top + Math.random() * box.height).toFixed(1)}px`,
        width: `${(sliver ? 2 : size).toFixed(1)}px`,
        height: `${(sliver ? size + 4 + Math.random() * 4 : size).toFixed(1)}px`,
        background: roll < 0.62 ? palette[0] : roll < 0.9 ? palette[1] : palette[2],
        '--particle-x': `${(Math.cos(angle) * distance).toFixed(1)}px`,
        '--particle-y': `${(Math.sin(angle) * distance * 0.8 - 18).toFixed(1)}px`,
        '--particle-rotate': `${Math.round((Math.random() - 0.5) * 180)}deg`,
        '--particle-delay': `${Math.round(Math.random() * 150)}ms`,
        '--particle-duration': `${Math.round(680 + Math.random() * 320)}ms`,
        '--particle-alpha': `${(0.78 + Math.random() * 0.22).toFixed(2)}`
      }
    })
  }

  particles.value = list
}

const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true

const TOTAL_MS = 2820
const REDUCED_MS = 1000
const GLITCH_AT_MS = 1000
const PARTICLE_END_MS = 2300
let timer: number | undefined
let glitchTimer: number | undefined
let particleCleanupTimer: number | undefined

onMounted(() => {
  timer = window.setTimeout(() => emit('done'), reducedMotion ? REDUCED_MS : TOTAL_MS)
  if (!reducedMotion) {
    glitchTimer = window.setTimeout(spawnParticles, GLITCH_AT_MS)
    particleCleanupTimer = window.setTimeout(() => {
      particles.value = []
    }, PARTICLE_END_MS)
  }
})

onBeforeUnmount(() => {
  if (timer !== undefined) {
    window.clearTimeout(timer)
  }
  if (glitchTimer !== undefined) {
    window.clearTimeout(glitchTimer)
  }
  if (particleCleanupTimer !== undefined) {
    window.clearTimeout(particleCleanupTimer)
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      class="login-reveal"
      :class="{ 'is-reduced': reducedMotion }"
      role="status"
      aria-live="polite"
      :aria-label="`${helloText}，${roleText}`"
      @wheel.prevent
      @touchmove.prevent
    >
      <div class="login-reveal__panel login-reveal__panel--top" aria-hidden="true">
        <span class="login-reveal__rule"></span>
      </div>
      <div class="login-reveal__panel login-reveal__panel--bottom" aria-hidden="true">
        <span class="login-reveal__rule"></span>
      </div>

      <div class="login-reveal__stage" aria-hidden="true">
        <p class="login-reveal__eyebrow">SYS // AUTHENTICATED</p>

        <p ref="helloLineRef" class="login-reveal__line login-reveal__line--hello">
          <span class="login-reveal__text">
            <span
              v-for="(glyph, index) in helloGlyphs"
              :key="`hello-${index}`"
              class="login-reveal__glyph"
              :class="{ 'is-accent': glyph.accent }"
              :style="glyph.style"
              >{{ glyph.char }}</span
            >
          </span>
        </p>

        <p ref="roleLineRef" class="login-reveal__line login-reveal__line--role">
          <span class="login-reveal__text">
            <span
              v-for="(glyph, index) in roleGlyphs"
              :key="`role-${index}`"
              class="login-reveal__glyph"
              :class="{ 'is-accent': glyph.accent }"
              :style="glyph.style"
              >{{ glyph.char }}</span
            >
          </span>
        </p>
      </div>

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
  --reveal-accent: #49f4e2;
  --reveal-accent-dim: rgba(73, 244, 226, 0.5);
  --reveal-panel: #05080b;
  position: fixed;
  inset: 0;
  z-index: 9500;
  overflow: hidden;
  background: transparent;
}

/* 上下两片幕布：接缝各带一条发光边。开场两条边完全重合，看上去只有一条线；
   拉开时随幕布向上下分离。 */
.login-reveal__panel {
  position: absolute;
  left: 0;
  width: 100%;
  height: 50.2%;
  background:
    repeating-linear-gradient(
      0deg,
      rgba(73, 244, 226, 0.045) 0 1px,
      transparent 1px 5px
    ),
    radial-gradient(120% 100% at 50% 50%, rgba(73, 244, 226, 0.11), transparent 64%),
    var(--reveal-panel);
  will-change: transform;
}

.login-reveal__panel--top {
  top: 0;
  animation: reveal-panel-up 660ms cubic-bezier(0.76, 0, 0.24, 1) 2120ms forwards;
}

.login-reveal__panel--bottom {
  bottom: 0;
  animation: reveal-panel-down 660ms cubic-bezier(0.76, 0, 0.24, 1) 2120ms forwards;
}

.login-reveal__rule {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(
    90deg,
    rgba(73, 244, 226, 0) 0%,
    rgba(73, 244, 226, 0.35) 10%,
    #49f4e2 34%,
    #d8fffa 50%,
    #49f4e2 66%,
    rgba(73, 244, 226, 0.35) 90%,
    rgba(73, 244, 226, 0) 100%
  );
  box-shadow:
    0 0 10px rgba(73, 244, 226, 0.6),
    0 0 28px rgba(73, 244, 226, 0.3);
  transform: scaleX(0);
  transform-origin: 50% 50%;
  animation: reveal-rule-grow 440ms cubic-bezier(0.19, 1, 0.22, 1) 1680ms forwards;
}

.login-reveal__panel--top .login-reveal__rule {
  bottom: -1px;
}

.login-reveal__panel--bottom .login-reveal__rule {
  top: -1px;
}

.login-reveal__stage {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  text-align: center;
  pointer-events: none;
}

/* 文字粒子：只做 transform / opacity；数量在脚本里按屏宽限制（手机 24 / 桌面 44） */
.login-reveal__particle {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  opacity: 0;
  will-change: transform, opacity;
  animation: reveal-particle-out var(--particle-duration, 760ms)
    cubic-bezier(0.16, 0.84, 0.32, 1) var(--particle-delay, 0ms) forwards;
}

@keyframes reveal-particle-out {
  0% {
    opacity: 0;
    transform: translate3d(0, 0, 0) scale(0.6);
  }
  8% {
    opacity: var(--particle-alpha, 0.9);
  }
  55% {
    opacity: var(--particle-alpha, 0.9);
  }
  100% {
    opacity: 0;
    transform: translate3d(var(--particle-x), var(--particle-y), 0)
      rotate(var(--particle-rotate, 0deg)) scale(0.3);
  }
}

.login-reveal__eyebrow {
  margin: 0 0 8px;
  color: rgba(73, 244, 226, 0.72);
  font-family: var(--yg-font-mono, ui-monospace, 'SFMono-Regular', 'Consolas', monospace);
  font-size: 11px;
  letter-spacing: 0.42em;
  animation: reveal-eyebrow-out 280ms steps(3, end) 1000ms forwards;
}

.login-reveal__line {
  position: relative;
  margin: 0;
  color: #f4f6f8;
  line-height: 1.25;
  white-space: nowrap;
  /* 与首页 ZVMS 字标同款：粉/青左右分离，静止时也能看出是故障字 */
  text-shadow:
    2px 0 rgba(255, 78, 155, 0.55),
    -2px 0 rgba(73, 244, 226, 0.6);
}

.login-reveal__line--hello {
  font-size: clamp(36px, 6.4vw, 78px);
  font-weight: 700;
  letter-spacing: 0.02em;
  text-shadow:
    3px 0 rgba(255, 78, 155, 0.55),
    -3px 0 rgba(73, 244, 226, 0.6);
}

.login-reveal__line--role {
  color: rgba(244, 246, 248, 0.72);
  font-size: clamp(17px, 2.4vw, 30px);
  font-weight: 600;
  letter-spacing: 0.14em;
}

.login-reveal__text {
  position: relative;
  z-index: 2;
  display: inline-block;
  will-change: translate, rotate;
  /* 每个关键帧停顿约 30ms：位移 150ms 一轮、倾斜 132ms 一轮，抖动频率比之前高得多；
     用 forwards 而不是 both，避免 1s 等待期被拉到首个偏移状态。 */
  animation:
    reveal-jitter 150ms steps(1, end) 1000ms 5 forwards,
    reveal-tilt 132ms steps(1, end) 1000ms 6 forwards;
}

.login-reveal__line--role .login-reveal__text {
  animation-delay: 1040ms, 1040ms;
}

.login-reveal__glyph {
  display: inline-block;
  will-change: opacity, filter, transform;
  animation: reveal-glyph-blur-out 580ms cubic-bezier(0.4, 0, 0.8, 0.4)
    calc(1000ms + var(--reveal-glyph-delay)) both;
}

.login-reveal__glyph.is-accent {
  color: var(--reveal-accent);
}

.login-reveal__line--hello .login-reveal__glyph.is-accent {
  text-shadow:
    3px 0 rgba(255, 78, 155, 0.5),
    -3px 0 rgba(73, 244, 226, 0.6),
    0 0 24px rgba(73, 244, 226, 0.35);
}

.login-reveal__line--role .login-reveal__glyph.is-accent {
  font-weight: 700;
  letter-spacing: 0.14em;
  text-shadow:
    2px 0 rgba(255, 78, 155, 0.45),
    -2px 0 rgba(73, 244, 226, 0.55),
    0 0 20px rgba(73, 244, 226, 0.3);
}

/* 位移与倾斜分开写：两套不同周期的关键帧叠加，避免抖动看起来有规律 */
@keyframes reveal-jitter {
  0% {
    translate: -8px 3px;
  }
  20% {
    translate: 7px -4px;
  }
  40% {
    translate: -5px 4px;
  }
  60% {
    translate: 8px -2px;
  }
  80% {
    translate: -4px -4px;
  }
  100% {
    translate: 5px 3px;
  }
}

@keyframes reveal-tilt {
  0% {
    rotate: -0.8deg;
  }
  25% {
    rotate: 0.9deg;
  }
  50% {
    rotate: -0.5deg;
  }
  75% {
    rotate: 0.7deg;
  }
  100% {
    rotate: -0.4deg;
  }
}

@keyframes reveal-glyph-blur-out {
  0% {
    opacity: 1;
    filter: blur(0);
    transform: scale(1);
  }
  18% {
    opacity: 0.95;
    filter: blur(1.6px);
    transform: translateY(-1px) scale(1.004);
  }
  28% {
    opacity: 0.82;
    filter: blur(0.4px);
    transform: scale(1.006);
  }
  42% {
    opacity: 0.7;
    filter: blur(3px);
    transform: translateY(1px) scale(1.012);
  }
  54% {
    opacity: 0.55;
    filter: blur(1.2px);
    transform: scale(1.016);
  }
  68% {
    opacity: 0.42;
    filter: blur(5px);
    transform: scale(1.024);
  }
  80% {
    opacity: 0.28;
    filter: blur(2.6px);
    transform: translateY(-1px) scale(1.03);
  }
  100% {
    opacity: 0;
    filter: blur(9px);
    transform: scale(1.05);
  }
}

@keyframes reveal-eyebrow-out {
  0% {
    opacity: 1;
    filter: blur(0);
  }
  45% {
    opacity: 0.3;
    filter: blur(1.5px);
  }
  100% {
    opacity: 0;
    filter: blur(4px);
  }
}

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

/* 减少动态：不留幕布与故障，短暂停留后整体淡出 */
.login-reveal.is-reduced {
  animation: reveal-fade-out 300ms ease 700ms forwards;
}

.login-reveal.is-reduced .login-reveal__panel,
.login-reveal.is-reduced .login-reveal__rule {
  animation: none;
  opacity: 0;
}

.login-reveal.is-reduced .login-reveal__text,
.login-reveal.is-reduced .login-reveal__glyph,
.login-reveal.is-reduced .login-reveal__eyebrow {
  animation: none;
}

@keyframes reveal-fade-out {
  to {
    opacity: 0;
  }
}
</style>

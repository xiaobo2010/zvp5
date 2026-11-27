<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

/**
 * 登录页背景：NERV × Phigros × Cyberpunk 的分层场景。
 *
 * 缓存策略（登录页首帧/循环动画不再卡顿的关键）：
 * 1. 静态层（网格、辉光、准星环、hazard 条、判定线、碎片）不是 CSS 渐变堆出来的，
 *    而是在模块级按主题生成一次 SVG data URI，之后所有登录页挂载都复用同一个字符串。
 *    浏览器把 data URI 解码成位图后进入图片缓存，滚动/主题切换不会重新栅格化。
 * 2. 动画层只保留少量 transform/opacity 合成层，没有 requestAnimationFrame、
 *    没有 canvas 逐帧绘制、没有每帧 getBoundingClientRect；标签页隐藏时统一暂停。
 * 3. Static art is intentionally independent of viewport size: the SVG uses a
 *    1440×900 viewBox and `background-size: cover`, so resize never rebuilds it.
 *
 * 封号页（ZBanScreen）的粒子退场是另一套运行时生成逻辑，本次不动。
 */

type BackdropTheme = 'dark' | 'light'

interface Palette {
  bg1: string
  bg2: string
  grid: string
  scan: string
  cyan: string
  pink: string
  warm: string
  ring: string
  line: string
  glowCyan: number
  glowPink: number
  hazard: number
  lineStrong: number
}

const BACKDROP_CACHE = new Map<BackdropTheme, string>()

function paletteFor(theme: BackdropTheme): Palette {
  if (theme === 'light') {
    return {
      bg1: '#f8fcfd',
      bg2: '#e8f1f3',
      grid: 'rgba(5, 6, 8, 0.055)',
      scan: 'rgba(5, 6, 8, 0.03)',
      cyan: '#007a70',
      pink: '#c2185b',
      warm: '#b45309',
      ring: 'rgba(0, 122, 112, 0.16)',
      line: 'rgba(5, 6, 8, 0.14)',
      glowCyan: 0.11,
      glowPink: 0.06,
      hazard: 0.08,
      lineStrong: 0.42
    }
  }
  return {
    bg1: '#050608',
    bg2: '#0a1418',
    grid: 'rgba(73, 244, 226, 0.045)',
    scan: 'rgba(73, 244, 226, 0.025)',
    cyan: '#49f4e2',
    pink: '#ff4e9b',
    warm: '#ff7a3c',
    ring: 'rgba(73, 244, 226, 0.14)',
    line: 'rgba(73, 244, 226, 0.16)',
    glowCyan: 0.14,
    glowPink: 0.08,
    hazard: 0.18,
    lineStrong: 0.55
  }
}

/**
 * Build the static scene once per theme. This is the expensive part: gradients,
 * ticks and hazard patterns. The result is a data URI cached by the browser and
 * by BACKDROP_CACHE, so a second login mount is essentially free.
 */
function buildBackdrop(theme: BackdropTheme): string {
  const c = paletteFor(theme)
  const cx = 1060
  const cy = 440
  const r = 250
  const ticks: string[] = []

  for (let i = 0; i < 48; i += 1) {
    const angle = (i * Math.PI) / 24
    const inner = i % 4 === 0 ? r - 24 : r - 10
    const x1 = (cx + Math.cos(angle) * r).toFixed(1)
    const y1 = (cy + Math.sin(angle) * r).toFixed(1)
    const x2 = (cx + Math.cos(angle) * inner).toFixed(1)
    const y2 = (cy + Math.sin(angle) * inner).toFixed(1)
    ticks.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`)
  }

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${c.bg1}"/>
      <stop offset="0.58" stop-color="${c.bg2}"/>
      <stop offset="1" stop-color="${c.bg1}"/>
    </linearGradient>
    <radialGradient id="glowCyan" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="${c.cyan}" stop-opacity="${c.glowCyan}"/>
      <stop offset="1" stop-color="${c.cyan}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowPink" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="${c.pink}" stop-opacity="${c.glowPink}"/>
      <stop offset="1" stop-color="${c.pink}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="judgment" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="${c.cyan}" stop-opacity="0"/>
      <stop offset="0.22" stop-color="${c.cyan}" stop-opacity="${c.lineStrong}"/>
      <stop offset="0.64" stop-color="${c.pink}" stop-opacity="${c.lineStrong}"/>
      <stop offset="1" stop-color="${c.cyan}" stop-opacity="0"/>
    </linearGradient>
    <pattern id="grid" width="44" height="44" patternUnits="userSpaceOnUse">
      <path d="M44 0H0V44" fill="none" stroke="${c.grid}" stroke-width="1"/>
    </pattern>
    <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">
      <rect width="4" height="1" fill="${c.scan}"/>
    </pattern>
    <pattern id="hazard" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
      <rect width="9" height="22" fill="${c.warm}" opacity="${c.hazard}"/>
    </pattern>
    <clipPath id="hazardClip">
      <polygon points="960,0 1440,0 1440,250 1300,300"/>
    </clipPath>
  </defs>

  <rect width="1440" height="900" fill="url(#bg)"/>
  <circle cx="1080" cy="220" r="440" fill="url(#glowCyan)"/>
  <circle cx="240" cy="780" r="420" fill="url(#glowPink)"/>
  <rect width="1440" height="900" fill="url(#grid)"/>
  <rect width="1440" height="900" fill="url(#scan)"/>

  <g fill="none" stroke="${c.ring}" stroke-width="1.2">
    <circle cx="${cx}" cy="${cy}" r="${r}"/>
    <circle cx="${cx}" cy="${cy}" r="${r - 34}" stroke-dasharray="2 12"/>
    <circle cx="${cx}" cy="${cy}" r="${r + 44}" stroke-opacity="0.55"/>
    ${ticks.join('')}
  </g>
  <g stroke="${c.line}" stroke-width="1" stroke-opacity="0.7">
    <line x1="${cx - r - 92}" y1="${cy}" x2="${cx + r + 92}" y2="${cy}"/>
    <line x1="${cx}" y1="${cy - r - 76}" x2="${cx}" y2="${cy + r + 92}"/>
  </g>

  <g clip-path="url(#hazardClip)">
    <rect x="960" y="-40" width="480" height="380" fill="url(#hazard)"/>
  </g>
  <path d="M960 0H1440V250L1300 300" fill="none" stroke="${c.warm}" stroke-opacity="0.22"/>

  <line x1="680" y1="575" x2="1440" y2="575" stroke="url(#judgment)" stroke-width="1.5"/>
  <rect x="968" y="568" width="14" height="14" transform="rotate(45 975 575)" fill="${c.cyan}" opacity="0.55"/>

  <g stroke="${c.cyan}" stroke-opacity="0.14" stroke-width="1">
    <line x1="116" y1="86" x2="360" y2="-62"/>
    <line x1="158" y1="128" x2="334" y2="18"/>
    <line x1="1210" y1="646" x2="1480" y2="492"/>
  </g>
  <g stroke="${c.pink}" stroke-opacity="0.14" stroke-width="1">
    <line x1="1174" y1="676" x2="1436" y2="526"/>
  </g>

  <g fill="none" stroke="${c.line}" stroke-opacity="0.5">
    <polygon points="806,104 890,82 974,134 974,238 890,290 806,238"/>
  </g>
</svg>`

  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

function readTheme(): BackdropTheme {
  if (typeof document === 'undefined') {
    return 'dark'
  }
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

function cachedBackdrop(theme: BackdropTheme): string {
  const hit = BACKDROP_CACHE.get(theme)
  if (hit) {
    return hit
  }
  const uri = buildBackdrop(theme)
  BACKDROP_CACHE.set(theme, uri)
  return uri
}

const theme = ref<BackdropTheme>(readTheme())
const motionPaused = ref(false)
const reducedMotion = ref(false)

const staticStyle = computed(() => ({
  backgroundImage: `url("${cachedBackdrop(theme.value)}")`
}))

let themeObserver: MutationObserver | undefined
let reducedMotionQuery: MediaQueryList | undefined

function handleVisibilityChange() {
  if (typeof document === 'undefined') {
    return
  }
  motionPaused.value = document.hidden
}

function handleReducedMotionChange(event: MediaQueryListEvent) {
  reducedMotion.value = event.matches
}

onMounted(() => {
  if (typeof window !== 'undefined' && window.matchMedia) {
    reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = reducedMotionQuery.matches
    reducedMotionQuery.addEventListener('change', handleReducedMotionChange)
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange)
    motionPaused.value = document.hidden

    // Theme flips are rare; rebuild only the SVG URI. The motion layer is CSS,
    // so nothing else has to be recreated.
    themeObserver = new MutationObserver(() => {
      const next = readTheme()
      if (next !== theme.value) {
        theme.value = next
      }
    })
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme']
    })
  }
})

onUnmounted(() => {
  themeObserver?.disconnect()
  reducedMotionQuery?.removeEventListener('change', handleReducedMotionChange)
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }
})
</script>

<template>
  <div class="z-login-backdrop">
    <!-- 静态层：模块级缓存的 SVG data URI，浏览器解码一次后复用 -->
    <div class="z-login-backdrop__static" :style="staticStyle" aria-hidden="true" />

    <!-- 动画层：仅 transform / opacity，由合成器播放，可整体暂停 -->
    <div
      class="z-login-backdrop__motion"
      :data-motion-paused="motionPaused || reducedMotion || undefined"
      aria-hidden="true"
    >
      <div class="z-login-backdrop__reticle" />
      <div class="z-login-backdrop__sweep" />
      <div class="z-login-backdrop__glow" />
      <div class="z-login-backdrop__shard z-login-backdrop__shard--1" />
      <div class="z-login-backdrop__shard z-login-backdrop__shard--2" />
      <div class="z-login-backdrop__shard z-login-backdrop__shard--3" />
      <div class="z-login-backdrop__scan" />
    </div>
  </div>
</template>

<style scoped>
.z-login-backdrop {
  --bd-cyan: 73, 244, 226;
  --bd-pink: 255, 78, 155;
  --bd-warm: 255, 122, 60;
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
  contain: layout paint style;
}

html[data-theme='light'] .z-login-backdrop {
  --bd-cyan: 0, 122, 112;
  --bd-pink: 194, 24, 91;
  --bd-warm: 180, 83, 9;
}

.z-login-backdrop__static,
.z-login-backdrop__motion {
  position: absolute;
  inset: 0;
}

.z-login-backdrop__static {
  background-position: center;
  background-repeat: no-repeat;
  background-size: cover;
}

.z-login-backdrop__motion {
  overflow: hidden;
  transform: translateZ(0);
}

.z-login-backdrop__motion[data-motion-paused] * {
  animation-play-state: paused !important;
}

/* 旋转准星环：与静态 SVG 的大环同轴，只让虚线层动，制造仪器感 */
.z-login-backdrop__reticle {
  position: absolute;
  top: 48.9%;
  left: 73.6%;
  width: 520px;
  height: 520px;
  margin-top: -260px;
  margin-left: -260px;
  border: 1px dashed rgba(var(--bd-cyan), 0.14);
  border-radius: 50%;
  animation: backdrop-reticle-spin 36s linear infinite;
  will-change: transform;
}

.z-login-backdrop__reticle::before,
.z-login-backdrop__reticle::after {
  content: '';
  position: absolute;
  background: rgba(var(--bd-cyan), 0.12);
}

.z-login-backdrop__reticle::before {
  top: 50%;
  left: 14%;
  width: 72%;
  height: 1px;
}

.z-login-backdrop__reticle::after {
  top: 14%;
  left: 50%;
  width: 1px;
  height: 72%;
}

@keyframes backdrop-reticle-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 斜向扫描带：Phigros 判定线的运动残影 */
.z-login-backdrop__sweep {
  position: absolute;
  top: -16%;
  left: 28%;
  width: 74%;
  height: 2px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(var(--bd-cyan), 0.12) 24%,
    rgba(var(--bd-cyan), 0.7) 50%,
    rgba(var(--bd-pink), 0.45) 72%,
    transparent 100%
  );
  box-shadow:
    0 0 18px rgba(var(--bd-cyan), 0.4),
    0 0 46px rgba(var(--bd-cyan), 0.16);
  transform: rotate(-33deg);
  animation: backdrop-sweep 10s cubic-bezier(0.45, 0, 0.55, 1) infinite;
  will-change: transform, opacity;
}

@keyframes backdrop-sweep {
  0%,
  100% {
    opacity: 0;
    transform: translate3d(-8%, -4%, 0) rotate(-33deg);
  }
  12% {
    opacity: 0.8;
  }
  86% {
    opacity: 0.7;
  }
  100% {
    opacity: 0;
    transform: translate3d(78%, 64%, 0) rotate(-33deg);
  }
}

/* 右上角冷光呼吸 */
.z-login-backdrop__glow {
  position: absolute;
  top: -14%;
  right: -8%;
  width: 64%;
  height: 66%;
  background: radial-gradient(
    ellipse at 68% 30%,
    rgba(var(--bd-cyan), 0.12) 0%,
    rgba(var(--bd-cyan), 0.05) 38%,
    transparent 70%
  );
  mix-blend-mode: screen;
  animation: backdrop-glow-pulse 6.5s ease-in-out infinite;
  will-change: opacity;
}

html[data-theme='light'] .z-login-backdrop__glow {
  mix-blend-mode: normal;
  opacity: 0.65;
}

@keyframes backdrop-glow-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}

/* 几何碎片：只做小幅漂移 */
.z-login-backdrop__shard {
  position: absolute;
  width: 1px;
  height: 92px;
  background: linear-gradient(
    180deg,
    transparent 0%,
    rgba(var(--bd-cyan), 0.32) 50%,
    transparent 100%
  );
  opacity: 0.35;
  will-change: transform, opacity;
}

.z-login-backdrop__shard--1 {
  top: 22%;
  left: 27%;
  transform: rotate(-15deg);
  animation: backdrop-shard-drift-1 8s ease-in-out infinite;
}

.z-login-backdrop__shard--2 {
  top: 63%;
  right: 22%;
  height: 68px;
  background: linear-gradient(
    180deg,
    transparent 0%,
    rgba(var(--bd-pink), 0.3) 50%,
    transparent 100%
  );
  transform: rotate(25deg);
  animation: backdrop-shard-drift-2 11s ease-in-out infinite;
}

.z-login-backdrop__shard--3 {
  top: 36%;
  right: 38%;
  height: 46px;
  transform: rotate(38deg);
  animation: backdrop-shard-drift-3 14s ease-in-out infinite;
}

@keyframes backdrop-shard-drift-1 {
  0%,
  100% {
    transform: translate3d(0, 0, 0) rotate(-15deg);
    opacity: 0.18;
  }
  50% {
    transform: translate3d(-18px, 12px, 0) rotate(-15deg);
    opacity: 0.5;
  }
}

@keyframes backdrop-shard-drift-2 {
  0%,
  100% {
    transform: translate3d(0, 0, 0) rotate(25deg);
    opacity: 0.24;
  }
  50% {
    transform: translate3d(14px, -10px, 0) rotate(25deg);
    opacity: 0.46;
  }
}

@keyframes backdrop-shard-drift-3 {
  0%,
  100% {
    transform: translate3d(0, 0, 0) rotate(38deg);
    opacity: 0.12;
  }
  50% {
    transform: translate3d(-10px, -16px, 0) rotate(38deg);
    opacity: 0.34;
  }
}

/* 缓慢下移的扫描带：视觉上像终端刷新，不触碰表单 */
.z-login-backdrop__scan {
  position: absolute;
  top: -22%;
  right: 8%;
  left: 8%;
  height: 22%;
  background: linear-gradient(
    180deg,
    transparent 0%,
    rgba(var(--bd-cyan), 0.035) 48%,
    rgba(var(--bd-cyan), 0.055) 50%,
    rgba(var(--bd-cyan), 0.01) 52%,
    transparent 100%
  );
  animation: backdrop-scan 16s linear infinite;
  will-change: transform;
}

@keyframes backdrop-scan {
  to {
    transform: translate3d(0, 560%, 0);
  }
}

@media (max-width: 959px) {
  .z-login-backdrop__reticle {
    top: auto;
    bottom: 2%;
    left: 50%;
    width: 300px;
    height: 300px;
    margin-top: 0;
    margin-left: -150px;
    opacity: 0.6;
  }

  .z-login-backdrop__sweep {
    left: -10%;
    width: 120%;
  }

  .z-login-backdrop__glow {
    top: -8%;
    right: -36%;
    width: 120%;
    height: 48%;
  }

  .z-login-backdrop__shard--1 {
    left: 12%;
  }

  .z-login-backdrop__shard--2 {
    right: 10%;
  }

  .z-login-backdrop__shard--3 {
    right: 22%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .z-login-backdrop__reticle,
  .z-login-backdrop__sweep,
  .z-login-backdrop__glow,
  .z-login-backdrop__shard,
  .z-login-backdrop__scan {
    animation: none !important;
    will-change: auto;
  }

  .z-login-backdrop__sweep,
  .z-login-backdrop__scan {
    opacity: 0;
  }
}
</style>

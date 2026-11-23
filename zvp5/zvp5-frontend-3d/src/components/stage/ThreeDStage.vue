<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import StarfieldCanvas from './StarfieldCanvas.vue'

import type { StageActivity, StageData, StageExtraItem } from '@/types/stage'

const props = defineProps<{ data: StageData }>()

const active = ref(0)
const previousActive = ref(0)
const transitioning = ref(false)
const reduced = ref(false)
const warp = ref(false)
const parallaxX = ref(0)
const parallaxY = ref(0)
const isTouch = ref(false)
let transitionTimer: ReturnType<typeof setTimeout> | undefined

let media: MediaQueryList | null = null

const GAP = 900
const TRANSITION_DUR = 820

const modules = computed(() => props.data.modules)
const total = computed(() => modules.value.length)
const maxHour = computed(() =>
  Math.max(1, ...props.data.hours.points.map((p) => p.hours))
)
const recent = computed(() => props.data.activities.slice(0, 4))
const extraItems = computed(() => props.data.extra.items.slice(0, 6))

function depthStyle(index: number) {
  const raw = index - active.value
  // A rail jump can skip modules; clamp the visual offset so a card never
  // travels behind the camera (z > perspective) or explodes in projection.
  const d = Math.max(-1, Math.min(1, raw))
  // Keep a tiny non-zero Z even for the active card so the browser serializes
  // every state as matrix3d; mixing matrix() and matrix3d() makes the depth
  // transition jump instead of interpolating.
  const z = -d * GAP + 0.01
  const scale = 1 - Math.abs(d) * 0.16
  const blur = Math.abs(d) * 10
  const opacity = raw === 0 ? 1 : 0
  const isVisible = transitioning.value
    ? index === active.value || index === previousActive.value
    : index === active.value

  return {
    transform: `translate(-50%, -50%) translate3d(0, 0, ${z}px) scale(${scale})`,
    filter: `blur(${blur}px)`,
    opacity: String(opacity),
    visibility: isVisible ? 'visible' : 'hidden',
    zIndex: String(100 - Math.abs(raw))
  } as const
}

function goTo(index: number) {
  if (index < 0 || index >= total.value || transitioning.value) return
  previousActive.value = active.value
  active.value = index
  transitioning.value = true
  warp.value = true

  if (transitionTimer) clearTimeout(transitionTimer)
  transitionTimer = setTimeout(() => {
    transitioning.value = false
    warp.value = false
  }, TRANSITION_DUR)
}

function go(delta: number) {
  goTo(active.value + delta)
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) {
    return
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    go(1)
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    go(-1)
  }
}

function onPointerMove(event: PointerEvent) {
  if (reduced.value || isTouch.value) return
  const x = (event.clientX / window.innerWidth - 0.5) * 2
  const y = (event.clientY / window.innerHeight - 0.5) * 2
  parallaxX.value = x * 3
  parallaxY.value = y * 3
}

function syncMotion(e: MediaQueryListEvent | MediaQueryList) {
  reduced.value = e.matches
}

function checkTouch() {
  isTouch.value = 'ontouchstart' in window || navigator.maxTouchPoints > 0
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  syncMotion(media)
  media.addEventListener('change', syncMotion)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('pointermove', onPointerMove)
  checkTouch()
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', syncMotion)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('pointermove', onPointerMove)
  if (transitionTimer) clearTimeout(transitionTimer)
})

function barDepth(index: number) {
  return {
    '--bar-z': `${index * -20}px`,
    '--bar-tilt': `${index === 0 ? -10 : index === 2 ? 10 : 0}deg`
  }
}

function activityDepth(index: number) {
  const depths = [0, -70, -140, -210]
  const tilts = [-4, 4, -3, 3]
  return {
    '--activity-z': `${depths[index] || 0}px`,
    '--activity-tilt': `${tilts[index] || 0}deg`
  }
}

function activityTone(activity: StageActivity) {
  const tones: Record<string, string> = {
    effective: '111, 215, 196',
    pending: '169, 211, 255',
    refused: '255, 198, 107',
    draft: '169, 211, 255'
  }
  return tones[activity.status] || tones.pending
}

function extraTone(item: StageExtraItem) {
  const tones: Record<string, string> = {
    blue: '169, 211, 255',
    teal: '111, 215, 196',
    violet: '169, 182, 255',
    amber: '255, 198, 107',
    rose: '255, 161, 173',
    muted: '138, 151, 179'
  }
  return tones[item.tone] || tones.blue
}
</script>

<template>
  <div class="stage" :class="{ 'is-reduced': reduced }">
    <!-- Background environment -->
    <div class="stage-backdrop">
      <div class="nebula nebula-1"></div>
      <div class="nebula nebula-2"></div>
      <div class="nebula nebula-3"></div>
      <div class="horizon-grid"></div>
    </div>

    <StarfieldCanvas :warp="warp" :reduced="reduced" />
    <div class="stage-shade" aria-hidden="true"></div>

    <!-- 3D scene -->
    <div class="stage-scene">
      <div
        class="stage-track"
        :style="{
          transform: reduced
            ? 'none'
            : `rotateX(${parallaxY * 0.3}deg) rotateY(${parallaxX * 0.3}deg)`
        }"
      >
        <div
          v-for="(module, index) in modules"
          :key="module.id"
          class="stage-card-wrapper"
          :style="depthStyle(index)"
          :aria-hidden="index !== active ? 'true' : undefined"
        >
          <div class="stage-card" :class="`module-${module.id}`">
            <div class="card-brackets">
              <svg class="bracket bracket-tl" width="20" height="20" viewBox="0 0 20 20">
                <path d="M20 0H4C1.8 0 0 1.8 0 4v16" stroke="currentColor" fill="none" />
              </svg>
              <svg class="bracket bracket-tr" width="20" height="20" viewBox="0 0 20 20">
                <path d="M0 0h16c2.2 0 4 1.8 4 4v16" stroke="currentColor" fill="none" />
              </svg>
              <svg class="bracket bracket-bl" width="20" height="20" viewBox="0 0 20 20">
                <path d="M20 20H4c-2.2 0-4-1.8-4-4V0" stroke="currentColor" fill="none" />
              </svg>
              <svg class="bracket bracket-br" width="20" height="20" viewBox="0 0 20 20">
                <path d="M0 20h16c2.2 0 4-1.8 4-4V0" stroke="currentColor" fill="none" />
              </svg>
            </div>

            <div class="card-glow"></div>

            <div class="card-header">
              <div class="card-eyebrow">{{ module.eyebrow }}</div>
              <h2 class="card-title">{{ module.title }}</h2>
            </div>

            <!-- Identity -->
            <div v-if="module.id === 'identity'" class="card-content identity-content">
              <div class="identity-hero">
                <p class="identity-greeting">{{ data.identity.greeting }}</p>
                <h3 class="identity-name">{{ data.identity.name }}</h3>
                <div v-if="data.identity.pendingCount > 0" class="identity-badge">
                  {{ data.identity.pendingCount }} 条待审批
                </div>
              </div>

              <div class="identity-grid">
                <div class="identity-field">
                  <span class="field-label">学号</span>
                  <span class="field-value">{{ data.identity.studentId }}</span>
                </div>
                <div class="identity-field">
                  <span class="field-label">班级</span>
                  <span class="field-value">{{ data.identity.className }}</span>
                </div>
                <div class="identity-field">
                  <span class="field-label">角色</span>
                  <span class="field-value">{{ data.identity.rolesLabel }}</span>
                </div>
                <div class="identity-field identity-field--featured">
                  <span class="field-label">累计时长</span>
                  <span class="field-value field-value--large">{{ data.identity.totalHours }}</span>
                  <span class="field-unit">h</span>
                </div>
              </div>
            </div>

            <!-- Hours -->
            <div v-else-if="module.id === 'hours'" class="card-content hours-content">
              <div class="hours-scene">
                <div class="hours-floor"></div>
                <div class="hours-chart">
                  <div
                    v-for="(point, idx) in data.hours.points"
                    :key="point.key"
                    class="hours-bar-container"
                    :style="barDepth(idx)"
                  >
                    <div class="hours-bar" :style="{ '--bar-height': point.hours / maxHour }">
                      <div class="bar-back"></div>
                      <div class="bar-top"></div>
                      <div class="bar-front"></div>
                    </div>
                    <div class="hours-bar-label">
                      <span class="bar-label-text">{{ point.shortLabel }}</span>
                      <span class="bar-label-value">{{ point.hours }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <div class="hours-summary">
                <span class="summary-label">总计</span>
                <span class="summary-value">{{ data.hours.total }}</span>
                <span class="summary-unit">h</span>
              </div>
            </div>

            <!-- Activities -->
            <div v-else-if="module.id === 'activities'" class="card-content activities-content">
              <svg class="activities-connections" viewBox="0 0 100 100" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="conn-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="rgb(169 211 255)" stop-opacity="0.3" />
                    <stop offset="100%" stop-color="rgb(111 215 196)" stop-opacity="0.1" />
                  </linearGradient>
                </defs>
                <path
                  d="M23 18 L77 25 L70 76 L27 68 Z"
                  fill="none"
                  stroke="url(#conn-grad)"
                  stroke-width="0.4"
                  vector-effect="non-scaling-stroke"
                />
                <path
                  d="M23 18 L70 76 M77 25 L27 68"
                  fill="none"
                  stroke="url(#conn-grad)"
                  stroke-width="0.3"
                  opacity="0.45"
                  vector-effect="non-scaling-stroke"
                />
              </svg>

              <div class="activities-space">
                <div
                  v-for="(activity, idx) in recent"
                  :key="activity.id"
                  class="activity-card"
                  :style="{
                    ...activityDepth(idx),
                    '--tone-rgb': activityTone(activity)
                  }"
                >
                  <div class="activity-status"></div>
                  <h4 class="activity-title">{{ activity.name }}</h4>
                  <div class="activity-meta">
                    <span>{{ activity.dateLabel }}</span>
                    <span class="meta-sep">·</span>
                    <span>{{ activity.place }}</span>
                  </div>
                  <div class="activity-hours">
                    {{ activity.hours !== null ? `${activity.hours}h` : '待记录' }}
                  </div>
                </div>
              </div>
            </div>

            <!-- Extra -->
            <div v-else-if="module.id === 'extra'" class="card-content extra-content">
              <div v-if="extraItems.length" class="extra-list">
                <div
                  v-for="item in extraItems"
                  :key="item.id"
                  class="extra-item"
                  :style="{ '--tone-rgb': extraTone(item) }"
                >
                  <div class="extra-dot"></div>
                  <div class="extra-info">
                    <h4 class="extra-title">{{ item.title }}</h4>
                    <p class="extra-meta">{{ item.meta }}</p>
                    <p v-if="item.detail" class="extra-detail">{{ item.detail }}</p>
                  </div>
                </div>
              </div>
              <div v-else class="extra-empty">
                {{ data.extra.emptyLabel }}
              </div>
            </div>
          </div>

          <div class="card-reflection"></div>
        </div>
      </div>
    </div>

    <!-- Navigation -->
    <div class="stage-nav">
      <button
        type="button"
        class="nav-btn nav-btn--prev"
        :disabled="transitioning || active === 0"
        @click="go(-1)"
        aria-label="上一个模块"
      >
        <svg width="16" height="16" viewBox="0 0 16 16">
          <path
            d="M10 3L5 8l5 5"
            stroke="currentColor"
            fill="none"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
        <span>上一个</span>
      </button>

      <div class="depth-rail">
        <div class="rail-track">
          <div class="rail-progress" :style="{ width: `${(active / (total - 1)) * 100}%` }"></div>
          <div class="rail-ticks">
            <button
              v-for="(module, idx) in modules"
              :key="module.id"
              type="button"
              class="rail-tick"
              :class="{ 'is-active': idx === active, 'is-past': idx < active }"
              @click="goTo(idx)"
              :aria-label="`转到${module.title}`"
              :aria-current="idx === active ? 'page' : undefined"
            ></button>
          </div>
        </div>
        <div class="rail-counter">
          <span class="counter-current">{{ String(active + 1).padStart(2, '0') }}</span>
          <span class="counter-sep">/</span>
          <span class="counter-total">{{ String(total).padStart(2, '0') }}</span>
        </div>
      </div>

      <button
        type="button"
        class="nav-btn nav-btn--next"
        :disabled="transitioning || active === total - 1"
        @click="go(1)"
        aria-label="下一个模块"
      >
        <span>下一个</span>
        <svg width="16" height="16" viewBox="0 0 16 16">
          <path
            d="M6 3l5 5-5 5"
            stroke="currentColor"
            fill="none"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </button>

      <div
        class="nav-announcement sr-only"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ transitioning ? '' : `当前模块: ${modules[active]?.title}` }}
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Scene Layout */
.stage {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 100vh;
  overflow: hidden;
  background: #04070d;
  --transition-dur: 820ms;
  --transition-ease: cubic-bezier(0.16, 1, 0.3, 1);
}

.stage-backdrop {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}

.nebula {
  position: absolute;
  border-radius: 50%;
  filter: blur(140px);
  opacity: 0.08;
}

.nebula-1 {
  top: -20%;
  left: -10%;
  width: 50%;
  height: 50%;
  background: radial-gradient(circle, rgb(109 180 255) 0%, transparent 60%);
}

.nebula-2 {
  top: 40%;
  right: -15%;
  width: 60%;
  height: 60%;
  background: radial-gradient(circle, rgb(111 215 196) 0%, transparent 60%);
}

.nebula-3 {
  bottom: -10%;
  left: 30%;
  width: 40%;
  height: 40%;
  background: radial-gradient(circle, rgb(169 182 255) 0%, transparent 60%);
}

.stage-shade {
  position: fixed;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background: radial-gradient(
    ellipse at 50% 45%,
    transparent 30%,
    rgb(4 7 13 / 0.45) 72%,
    rgb(4 7 13 / 0.88) 100%
  );
}

.horizon-grid {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  height: 30%;
  background: linear-gradient(
    to top,
    transparent 0%,
    rgb(169 211 255 / 0.02) 50%,
    transparent 100%
  );
  background-size: 40px 40px;
  background-position: center bottom;
  opacity: 0.15;
  transform: perspective(400px) rotateX(60deg);
  transform-origin: bottom;
}

/* 3D Scene */
.stage-scene {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-height: 100vh;
  padding: 72px 0 104px;
  display: flex;
  align-items: center;
  justify-content: center;
  perspective: 1400px;
  z-index: 10;
}

.stage-track {
  position: relative;
  width: 100%;
  height: 100%;
  transform-style: preserve-3d;
  transition: transform 300ms ease-out;
}

.is-reduced .stage-track {
  transform: none !important;
}

.stage-card-wrapper {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 100%;
  max-width: 620px;
  transform-style: preserve-3d;
  transition: all var(--transition-dur) var(--transition-ease);
  will-change: transform, opacity, filter;
}

/* Card Base */
.stage-card {
  position: relative;
  margin: 0 auto;
  box-sizing: border-box;
  padding: 48px 52px;
  background: linear-gradient(
    135deg,
    rgb(16 24 42 / 0.82) 0%,
    rgb(10 14 24 / 0.9) 100%
  );
  border: 1px solid rgb(169 211 255 / 0.12);
  border-radius: 2px;
  backdrop-filter: blur(20px);
  transform-style: preserve-3d;
  animation: card-bob 4s ease-in-out infinite;
  animation-play-state: var(--bob-play, running);
  box-shadow:
    0 40px 120px rgb(0 0 0 / 0.5),
    inset 0 1px 0 rgb(169 211 255 / 0.08);
}

.is-reduced .stage-card {
  animation: none;
}

@keyframes card-bob {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-6px);
  }
}

.card-brackets {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.bracket {
  position: absolute;
  width: 20px;
  height: 20px;
  color: rgb(169 211 255 / 0.3);
  stroke-width: 1;
}

.bracket-tl {
  top: 12px;
  left: 12px;
}
.bracket-tr {
  top: 12px;
  right: 12px;
}
.bracket-bl {
  bottom: 12px;
  left: 12px;
}
.bracket-br {
  bottom: 12px;
  right: 12px;
}

.card-glow {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%) translateZ(-2px);
  width: 70%;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    rgb(169 211 255 / 0.4),
    transparent
  );
  box-shadow: 0 0 20px rgb(169 211 255 / 0.3);
}

.card-reflection {
  position: absolute;
  bottom: -60px;
  left: 50%;
  transform: translateX(-50%) translateZ(-10px);
  width: 90%;
  height: 40px;
  background: linear-gradient(
    to top,
    transparent,
    rgb(169 211 255 / 0.03)
  );
  filter: blur(20px);
  opacity: 0.6;
}

/* Card Header */
.card-header {
  margin-bottom: 36px;
  transform: translateZ(10px);
}

.card-eyebrow {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.24em;
  color: rgb(169 211 255 / 0.6);
  text-transform: uppercase;
  margin-bottom: 10px;
}

.card-title {
  font-size: 28px;
  font-weight: 300;
  letter-spacing: -0.02em;
  color: #eef6ff;
}

.card-content {
  flex: 1;
  transform: translateZ(5px);
}

/* Identity Module */
.identity-content {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.identity-hero {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 24px;
  border-bottom: 1px solid rgb(169 211 255 / 0.08);
}

.identity-greeting {
  font-size: 13px;
  color: rgb(169 211 255 / 0.5);
  letter-spacing: 0.03em;
}

.identity-name {
  font-size: 36px;
  font-weight: 200;
  letter-spacing: -0.03em;
  color: #eef6ff;
}

.identity-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  font-size: 12px;
  letter-spacing: 0.02em;
  color: #ffc46b;
  background: rgb(255 196 107 / 0.08);
  border: 1px solid rgb(255 196 107 / 0.2);
  border-radius: 999px;
  width: fit-content;
}

.identity-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.identity-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  background: rgb(16 24 42 / 0.3);
  border: 1px solid rgb(169 211 255 / 0.06);
  border-radius: 1px;
}

.identity-field--featured {
  grid-column: 1 / -1;
  flex-direction: row;
  align-items: baseline;
  gap: 12px;
}

.field-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.18em;
  color: rgb(169 211 255 / 0.4);
  text-transform: uppercase;
}

.field-value {
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 400;
  color: #a9d3ff;
  letter-spacing: 0.02em;
}

.field-value--large {
  font-size: 48px;
  font-weight: 200;
  color: #eef6ff;
}

.field-unit {
  font-size: 20px;
  font-weight: 300;
  color: rgb(169 211 255 / 0.5);
}

/* Hours Module */
.hours-content {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.hours-scene {
  position: relative;
  height: 240px;
  transform-style: preserve-3d;
}

.hours-floor {
  position: absolute;
  left: -10%;
  right: -10%;
  bottom: 26px;
  height: 150px;
  background-image:
    linear-gradient(rgb(169 211 255 / 0.14) 1px, transparent 1px),
    linear-gradient(90deg, rgb(169 211 255 / 0.14) 1px, transparent 1px);
  background-size: 48px 22px;
  transform: perspective(420px) rotateX(66deg);
  transform-origin: 50% 100%;
  mask-image: linear-gradient(to top, #000 10%, transparent 90%);
  opacity: 0.6;
  pointer-events: none;
}

.hours-chart {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  height: 100%;
  gap: 24px;
  transform: rotateX(-16deg);
  transform-origin: 50% 100%;
  transform-style: preserve-3d;
  perspective: 600px;
}

.hours-bar-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  flex: 1;
  transform: translateZ(var(--bar-z, 0)) rotateY(var(--bar-tilt, 0deg));
  transform-style: preserve-3d;
}

.hours-bar {
  position: relative;
  width: 100%;
  height: calc(var(--bar-height, 0.5) * 180px);
  min-height: 24px;
  transform-style: preserve-3d;
}

.bar-back,
.bar-top,
.bar-front {
  position: absolute;
}

.hours-bar::before,
.hours-bar::after {
  content: '';
  position: absolute;
  top: 0;
  width: 30px;
  height: 100%;
  background: linear-gradient(90deg, rgb(60 110 170 / 0.55), rgb(20 40 70 / 0.85));
  border: 1px solid rgb(169 211 255 / 0.12);
}

.hours-bar::before {
  right: 0;
  transform-origin: right center;
  transform: rotateY(-90deg);
}

.hours-bar::after {
  left: 0;
  transform-origin: left center;
  transform: rotateY(90deg);
}

.bar-back {
  inset: 0;
  background: rgb(16 24 42 / 0.6);
  border: 1px solid rgb(169 211 255 / 0.08);
  transform: translateZ(-30px);
}

.bar-top {
  top: 0;
  left: 0;
  width: 100%;
  height: 30px;
  background: linear-gradient(
    to bottom,
    rgb(169 211 255 / 0.12),
    rgb(169 211 255 / 0.04)
  );
  border: 1px solid rgb(169 211 255 / 0.1);
  transform: rotateX(-90deg);
  transform-origin: top;
}

.bar-front {
  inset: 0;
  background: linear-gradient(
    180deg,
    rgb(169 211 255 / 0.4) 0%,
    rgb(169 211 255 / 0.15) 100%
  );
  border: 1px solid rgb(169 211 255 / 0.2);
  box-shadow: inset 0 1px 20px rgb(169 211 255 / 0.1);
}

.hours-bar-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  text-align: center;
}

.bar-label-text {
  font-size: 10px;
  letter-spacing: 0.18em;
  color: rgb(169 211 255 / 0.4);
  text-transform: uppercase;
}

.bar-label-value {
  font-size: 20px;
  font-weight: 200;
  color: #a9d3ff;
}

.hours-summary {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding-top: 20px;
  border-top: 1px solid rgb(169 211 255 / 0.08);
}

.summary-label {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.2em;
  color: rgb(169 211 255 / 0.4);
  text-transform: uppercase;
}

.summary-value {
  font-size: 44px;
  font-weight: 200;
  color: #eef6ff;
}

.summary-unit {
  font-size: 18px;
  font-weight: 300;
  color: rgb(169 211 255 / 0.5);
}

/* Activities Module */
.activities-content {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 280px;
  transform: perspective(900px) rotateX(-6deg) rotateY(3deg);
  transform-style: preserve-3d;
}

.activities-connections {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  opacity: 0.65;
}

.activities-space {
  position: relative;
  width: 100%;
  height: 320px;
  transform-style: preserve-3d;
}

.activity-card {
  position: absolute;
  width: 46%;
  padding: 16px;
  background: rgb(16 24 42 / 0.55);
  border: 1px solid rgb(var(--tone-rgb) / 0.18);
  border-radius: 1px;
  transform: translateZ(var(--activity-z, 0)) rotateY(var(--activity-tilt, 0deg));
  transform-style: preserve-3d;
  transition: all 300ms ease-out;
}

.activity-card:nth-child(1) {
  top: 0;
  left: 0;
}

.activity-card:nth-child(2) {
  top: 7%;
  right: 0;
}

.activity-card:nth-child(3) {
  bottom: 6%;
  left: 4%;
}

.activity-card:nth-child(4) {
  bottom: 0;
  right: 2%;
}

.activity-card:hover {
  background: rgb(16 24 42 / 0.6);
  border-color: rgb(var(--tone-rgb) / 0.3);
  box-shadow: 0 0 20px rgb(var(--tone-rgb) / 0.15);
}

.activity-status {
  position: absolute;
  top: 16px;
  right: 16px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgb(var(--tone-rgb));
  box-shadow: 0 0 8px rgb(var(--tone-rgb) / 0.6);
}

.activity-title {
  font-size: 15px;
  font-weight: 400;
  color: #eef6ff;
  margin-bottom: 8px;
  letter-spacing: -0.01em;
}

.activity-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  color: rgb(169 211 255 / 0.4);
  letter-spacing: 0.02em;
  margin-bottom: 8px;
}

.meta-sep {
  margin: 0 6px;
  opacity: 0.5;
}

.activity-hours {
  font-family: var(--font-mono);
  font-size: 13px;
  color: #a9d3ff;
  letter-spacing: 0.05em;
}

/* Extra Module */
.extra-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: safe center;
  overflow-y: auto;
  padding-right: 8px;
}

.extra-content::-webkit-scrollbar {
  width: 4px;
}

.extra-content::-webkit-scrollbar-track {
  background: rgb(16 24 42 / 0.3);
}

.extra-content::-webkit-scrollbar-thumb {
  background: rgb(169 211 255 / 0.2);
  border-radius: 2px;
}

.extra-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.extra-item {
  display: flex;
  gap: 14px;
  padding: 14px;
  background: rgb(16 24 42 / 0.3);
  border: 1px solid rgb(var(--tone-rgb) / 0.12);
  border-radius: 1px;
  transition: all 200ms ease-out;
}

.extra-item:hover {
  background: rgb(16 24 42 / 0.5);
  border-color: rgb(var(--tone-rgb) / 0.25);
}

.extra-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgb(var(--tone-rgb));
  box-shadow: 0 0 8px rgb(var(--tone-rgb) / 0.4);
  flex-shrink: 0;
  margin-top: 5px;
}

.extra-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.extra-title {
  font-size: 14px;
  font-weight: 400;
  color: #eef6ff;
  letter-spacing: -0.01em;
}

.extra-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  color: rgb(169 211 255 / 0.4);
  letter-spacing: 0.02em;
}

.extra-detail {
  font-size: 12px;
  color: rgb(169 211 255 / 0.5);
  margin-top: 2px;
}

.extra-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  font-family: var(--font-mono);
  font-size: 13px;
  color: rgb(169 211 255 / 0.3);
  letter-spacing: 0.05em;
}

/* Navigation */
.stage-nav {
  position: fixed;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 28px;
  z-index: 100;
}

.nav-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  color: #a9d3ff;
  background: rgb(16 24 42 / 0.6);
  border: 1px solid rgb(169 211 255 / 0.15);
  border-radius: 1px;
  backdrop-filter: blur(16px);
  cursor: pointer;
  transition: all 200ms ease-out;
}

.nav-btn:hover:not(:disabled) {
  background: rgb(16 24 42 / 0.8);
  border-color: rgb(169 211 255 / 0.3);
  color: #eef6ff;
  box-shadow: 0 0 20px rgb(169 211 255 / 0.1);
}

.nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.nav-btn svg {
  width: 14px;
  height: 14px;
}

/* Depth Rail */
.depth-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  background: rgb(16 24 42 / 0.6);
  border: 1px solid rgb(169 211 255 / 0.15);
  border-radius: 1px;
  backdrop-filter: blur(16px);
}

.rail-track {
  position: relative;
  width: 160px;
  height: 2px;
  background: rgb(169 211 255 / 0.1);
  border-radius: 1px;
  overflow: visible;
}

.rail-progress {
  position: absolute;
  left: 0;
  top: 0;
  height: 100%;
  background: linear-gradient(90deg, #6fd7e8, #a9d3ff);
  transition: width var(--transition-dur) var(--transition-ease);
  box-shadow: 0 0 8px rgb(169 211 255 / 0.4);
}

.rail-ticks {
  position: absolute;
  top: 50%;
  left: 0;
  width: 100%;
  display: flex;
  justify-content: space-between;
  transform: translateY(-50%);
}

.rail-tick {
  position: relative;
  width: 8px;
  height: 8px;
  padding: 0;
  background: rgb(16 24 42 / 0.8);
  border: 1px solid rgb(169 211 255 / 0.2);
  border-radius: 50%;
  cursor: pointer;
  transition: all 200ms ease-out;
}

.rail-tick::after {
  content: '';
  position: absolute;
  inset: -8px;
}

.rail-tick.is-past,
.rail-tick.is-active {
  background: #a9d3ff;
  border-color: #a9d3ff;
  box-shadow: 0 0 8px rgb(169 211 255 / 0.5);
}

.rail-tick:hover:not(.is-active) {
  background: rgb(169 211 255 / 0.4);
  border-color: rgb(169 211 255 / 0.4);
}

.rail-counter {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.15em;
  color: rgb(169 211 255 / 0.5);
  display: flex;
  align-items: center;
  gap: 6px;
}

.counter-current {
  color: #a9d3ff;
}

.counter-sep {
  opacity: 0.4;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

/* Responsive */
@media (max-width: 720px) {
  .stage-scene {
    padding: 64px 0 92px;
  }

  .stage-card-wrapper {
    width: calc(100% - 28px);
  }

  .stage-card {
    max-height: calc(100dvh - 180px);
    min-height: auto;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 36px 28px;
  }

  .card-title {
    font-size: 22px;
  }

  .identity-name {
    font-size: 28px;
  }

  .field-value--large {
    font-size: 36px;
  }

  .identity-grid {
    grid-template-columns: 1fr;
  }

  .hours-scene {
    height: 180px;
  }

  .hours-bar {
    height: calc(var(--bar-height, 0.5) * 120px);
  }

  .bar-label-value {
    font-size: 16px;
  }

  .summary-value {
    font-size: 32px;
  }

  .activities-content {
    transform: none;
  }

  .activities-space {
    height: auto;
    display: grid;
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .activities-connections {
    display: none;
  }

  .activity-card {
    position: static;
    width: auto;
    transform: none;
  }

  .stage-nav {
    bottom: 20px;
    gap: 16px;
  }

  .nav-btn {
    padding: 10px 16px;
    font-size: 11px;
    gap: 8px;
  }

  .nav-btn span {
    display: none;
  }

  .depth-rail {
    padding: 12px 16px;
  }

  .rail-track {
    width: 120px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stage-card {
    animation: none;
  }

  .stage-track {
    transform: none !important;
  }

  .stage-card-wrapper {
    transition: opacity var(--transition-dur) ease-out !important;
    transform: none !important;
    filter: none !important;
  }
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { StageActivity, StageData, StageExtraItem } from '@/types/stage'

const props = defineProps<{ data: StageData }>()

const active = ref(0)
const reduced = ref(false)
const transitioning = ref(false)
let media: MediaQueryList | null = null
let transitionTimer: ReturnType<typeof setTimeout> | undefined

const modules = computed(() => props.data.modules)
const total = computed(() => modules.value.length)
const current = computed(() => modules.value[active.value] ?? modules.value[0])
const maxHour = computed(() =>
  Math.max(1, ...props.data.hours.points.map((point) => point.hours))
)
const recent = computed(() => props.data.activities.slice(0, 4))
const extraItems = computed(() => props.data.extra.items.slice(0, 4))

function goTo(index: number) {
  if (index < 0 || index >= total.value || transitioning.value) return
  active.value = index
  transitioning.value = true
  if (transitionTimer) clearTimeout(transitionTimer)
  transitionTimer = setTimeout(() => {
    transitioning.value = false
  }, 800)
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

function syncMotion(event: MediaQueryListEvent | MediaQueryList) {
  reduced.value = event.matches
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  syncMotion(media)
  media.addEventListener('change', syncMotion)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', syncMotion)
  window.removeEventListener('keydown', onKeydown)
  if (transitionTimer) clearTimeout(transitionTimer)
})

function cardStyle(index: number) {
  const offset = index - active.value
  const abs = Math.abs(offset)
  const depth = abs === 0 ? 110 : abs === 1 ? -160 : -360
  const scale = abs === 0 ? 1 : abs === 1 ? 0.88 : 0.72
  const opacity = abs === 0 ? 1 : abs === 1 ? 0.52 : 0.18
  const rotY = offset * 6.5
  return {
    '--offset': String(offset),
    transform: `translate(-50%, -50%) translate3d(calc(var(--offset) * var(--step)), 0, ${depth}px) rotateY(${rotY}deg) scale(${scale})`,
    opacity: String(abs > 2 ? 0 : opacity),
    zIndex: String(20 - abs),
    animationDelay: `${index * -1.7}s`
  }
}

function barStyle(hours: number, index: number) {
  const ratio = Math.max(0.04, hours / maxHour.value)
  return {
    '--bar': `${Math.round(ratio * 100)}%`,
    '--tilt': `${index === 0 ? 6.5 : index === 2 ? -6.5 : 0}deg`,
    '--delay': `${index * 120}ms`
  }
}

function toneClass(item: StageExtraItem) {
  return `tone-${item.tone}`
}

function activityTone(activity: StageActivity) {
  return `activity--${activity.status}`
}

function getNodeRgb(activity: StageActivity): string {
  const tones: Record<string, string> = {
    draft: '168, 139, 255',
    pending: '109, 180, 255',
    refused: '255, 125, 146',
    effective: '79, 214, 196'
  }
  return tones[activity.status] || tones.pending
}
</script>

<template>
  <div class="stage" :class="{ 'is-reduced': reduced }">
    <p class="sr-only" aria-live="polite">
      {{ current.title }}：{{ current.summary }}（第 {{ active + 1 }} / {{ total }} 个模块）
    </p>
    <div class="stage__viewport">
      <div class="stage__container">
        <div
          v-for="(module, index) in modules"
          :key="module.id"
          class="stage__card"
          :class="[`module-${module.id}`, { 'is-active': index === active }]"
          :style="cardStyle(index)"
          :aria-hidden="index !== active"
        >
          <!-- Identity Module -->
          <template v-if="module.id === 'identity'">
            <div class="card-header">
              <div class="card-eyebrow">{{ module.eyebrow }}</div>
              <h2 class="card-title">{{ module.title }}</h2>
            </div>
            <div class="card-content">
              <div class="identity-greeting">
                <p class="identity-greeting__text">{{ data.identity.greeting }}</p>
                <p class="identity-name">{{ data.identity.name }}</p>
                <p v-if="data.identity.pendingCount" class="identity-pending">
                  {{ data.identity.pendingCount }} 条待审核
                </p>
              </div>
              <div class="identity-grid">
                <div class="identity-cell">
                  <span class="identity-label">学号</span>
                  <span class="identity-value">{{ data.identity.studentId }}</span>
                </div>
                <div class="identity-cell">
                  <span class="identity-label">班级</span>
                  <span class="identity-value">{{ data.identity.className }}</span>
                </div>
                <div class="identity-cell">
                  <span class="identity-label">角色</span>
                  <span class="identity-value">{{ data.identity.rolesLabel }}</span>
                </div>
                <div class="identity-cell">
                  <span class="identity-label">累计时长</span>
                  <span class="identity-value">{{ data.identity.totalHours }}h</span>
                </div>
              </div>
            </div>
          </template>

          <!-- Hours Module -->
          <template v-else-if="module.id === 'hours'">
            <div class="card-header">
              <div class="card-eyebrow">{{ module.eyebrow }}</div>
              <h2 class="card-title">{{ module.title }}</h2>
            </div>
            <div class="card-content">
              <div class="hours-chart">
                <div
                  v-for="(point, index) in data.hours.points"
                  :key="point.key"
                  class="hours-bar-wrapper"
                  :style="barStyle(point.hours, index)"
                >
                  <div class="hours-bar"></div>
                  <div class="hours-label">
                    <span class="hours-label__short">{{ point.shortLabel }}</span>
                    <span class="hours-label__value">{{ point.hours }}h</span>
                  </div>
                </div>
              </div>
              <div class="hours-total">
                <span class="hours-total__label">总计</span>
                <span class="hours-total__value">{{ data.hours.total }}h</span>
              </div>
            </div>
          </template>

          <!-- Activities Module -->
          <template v-else-if="module.id === 'activities'">
            <div class="card-header">
              <div class="card-eyebrow">{{ module.eyebrow }}</div>
              <h2 class="card-title">{{ module.title }}</h2>
            </div>
            <div class="card-content">
              <div v-if="!recent.length" class="activity-empty">
                还没有最近的义工记录。
              </div>
              <div v-else class="activities-constellation">
                <svg
                  class="activities-beam"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  aria-hidden="true"
                >
                  <defs>
                    <linearGradient id="beam-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stop-color="rgb(142 178 255)" />
                      <stop offset="50%" stop-color="rgb(142 178 255 / 0.12)" />
                      <stop offset="100%" stop-color="rgb(79 214 196)" />
                    </linearGradient>
                  </defs>
                  <path
                    d="M10 16 L90 16 L90 84 L10 84 Z"
                    fill="none"
                    stroke="url(#beam-gradient)"
                    stroke-width="0.7"
                    vector-effect="non-scaling-stroke"
                    opacity="0.55"
                  />
                  <path
                    d="M10 16 L90 84 M90 16 L10 84"
                    fill="none"
                    stroke="url(#beam-gradient)"
                    stroke-width="0.5"
                    vector-effect="non-scaling-stroke"
                    opacity="0.22"
                  />
                </svg>
                <div
                  v-for="(activity, idx) in recent"
                  :key="activity.id"
                  class="activity-node"
                  :class="[activityTone(activity), `node-pos-${idx}`]"
                  :style="{ '--node-rgb': getNodeRgb(activity) }"
                >
                  <div class="node-dot"></div>
                  <div class="node-info">
                    <p class="node-title">{{ activity.name }}</p>
                    <p class="node-meta">
                      {{ activity.dateLabel }}<template v-if="activity.place"> · {{ activity.place }}</template>
                    </p>
                    <p class="node-foot">
                      <span>{{ activity.statusLabel }}</span>
                      <span>{{ activity.hours === null ? '待记录' : activity.hours + 'h' }}</span>
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- Extra Module -->
          <template v-else-if="module.id === 'extra'">
            <div class="card-header">
              <div class="card-eyebrow">{{ module.eyebrow }}</div>
              <h2 class="card-title">{{ module.title }}</h2>
            </div>
            <div class="card-content">
              <div v-if="extraItems.length" class="extra-list">
                <div
                  v-for="item in extraItems"
                  :key="item.id"
                  class="extra-item"
                  :class="toneClass(item)"
                >
                  <div class="extra-item__dot"></div>
                  <div class="extra-item__content">
                    <p class="extra-item__title">{{ item.title }}</p>
                    <p class="extra-item__meta">{{ item.meta }}</p>
                    <p class="extra-item__detail">{{ item.detail }}</p>
                  </div>
                </div>
              </div>
              <div v-else class="extra-empty">
                {{ data.extra.emptyLabel }}
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Navigation -->
    <div class="stage__nav">
      <button
        type="button"
        class="nav-button nav-button--prev"
        @click="go(-1)"
        :disabled="transitioning || active === 0"
        aria-label="上一个模块"
      >
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M12 5L7 10l5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span>上一个</span>
      </button>

      <div class="nav-dots">
        <button
          v-for="(module, idx) in modules"
          :key="module.id"
          type="button"
          class="nav-dot"
          :class="{ 'is-active': idx === active }"
          @click="goTo(idx)"
          :aria-label="`转到${module.title}`"
          :aria-current="idx === active ? 'page' : undefined"
        ></button>
        <span class="nav-counter">{{ active + 1 }} / {{ total }}</span>
      </div>

      <button
        type="button"
        class="nav-button nav-button--next"
        @click="go(1)"
        :disabled="transitioning || active === total - 1"
        aria-label="下一个模块"
      >
        <span>下一个</span>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M8 5l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* Layout & Perspective */
.stage {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 100vh;
  min-height: 100dvh;
  box-sizing: border-box;
  padding: clamp(84px, 10vh, 120px) 0 clamp(12px, 2vh, 24px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  --step: 420px;
  --transition-ease: var(--ease-out);
  --transition-dur: 700ms;
}

.stage__viewport {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  perspective: 1200px;
}

.stage__container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-style: preserve-3d;
}

/* Card Base */
.stage__card {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 100%;
  max-width: 540px;
  height: auto;
  min-height: 420px;
  padding: 40px;
  display: flex;
  flex-direction: column;
  gap: 32px;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, rgb(28 42 66 / 0.5) 0%, rgb(16 22 36 / 0.6) 100%);
  backdrop-filter: blur(16px);
  border: 1px solid rgb(142 178 255 / 0.14);
  color: var(--color-ink);
  font-size: 15px;
  line-height: 1.6;
  transition: all var(--transition-dur) var(--transition-ease);
  will-change: transform, opacity;
  animation: card-float var(--stage-float-dur, 3.2s) ease-in-out infinite;
  animation-play-state: var(--stage-float-play, running);

  /* Luminous rim using pseudo-element with gradient mask */
  &::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: var(--radius-lg);
    padding: 1px;
    background: linear-gradient(135deg, rgb(142 178 255 / 0.4) 0%, rgb(109 180 255 / 0.1) 50%, rgb(79 214 196 / 0.2) 100%);
    -webkit-mask: linear-gradient(#fff 0%, #fff calc(100% - 1px), transparent calc(100% - 1px));
    mask: linear-gradient(#fff 0%, #fff calc(100% - 1px), transparent calc(100% - 1px));
    opacity: 0.6;
    pointer-events: none;
  }

  /* Top glow */
  &::after {
    content: '';
    position: absolute;
    top: -1px;
    left: 50%;
    transform: translateX(-50%);
    width: 60%;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgb(109 180 255 / 0.4), transparent);
    opacity: 0.4;
    pointer-events: none;
  }

  &.is-active {
    --stage-float-play: running;
    box-shadow: 0 20px 80px rgb(109 180 255 / 0.2), inset 0 1px 24px rgb(142 178 255 / 0.08);

    &::before {
      opacity: 1;
      background: linear-gradient(135deg, rgb(142 178 255 / 0.6) 0%, rgb(109 180 255 / 0.2) 50%, rgb(79 214 196 / 0.3) 100%);
    }

    &::after {
      opacity: 0.6;
    }
  }

  &:not(.is-active) {
    --stage-float-play: paused;
  }
}

.is-reduced .stage__card {
  animation: none;
}

.is-reduced .hours-bar::before,
.is-reduced .activity-node {
  animation: none;
  opacity: 1;
}

@keyframes card-float {
  0%,
  100% {
    translate: 0 0;
  }
  50% {
    translate: 0 -8px;
  }
}

/* Card Content Structure */
.card-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-eyebrow {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.08em;
  color: var(--color-accent);
  opacity: 0.8;
  text-transform: uppercase;
}

.card-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-ink);
  letter-spacing: -0.02em;
}

.card-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* Identity Module */
.identity-greeting {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--color-line);
}

.identity-greeting__text {
  font-size: 13px;
  color: var(--color-ink-soft);
}

.identity-name {
  font-size: 20px;
  font-weight: 600;
  color: var(--color-ink);
  letter-spacing: -0.01em;
}

.identity-pending {
  align-self: flex-start;
  margin-top: 8px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid rgb(255 196 107 / 0.28);
  background: rgb(255 196 107 / 0.12);
  color: var(--tone-amber);
  font-size: 11px;
  letter-spacing: 0.06em;
}

.identity-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.identity-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background: rgb(16 22 36 / 0.4);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-line);
}

.identity-label {
  font-size: 11px;
  letter-spacing: 0.05em;
  color: var(--color-ink-soft);
  text-transform: uppercase;
}

.identity-value {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 500;
  color: var(--color-accent);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Hours Module */
.hours-chart {
  display: flex;
  flex-direction: row;
  align-items: flex-end;
  justify-content: space-around;
  gap: clamp(12px, 2.5vw, 28px);
  height: 220px;
  min-height: 170px;
  padding: 0 4px;
  border-bottom: 1px solid var(--color-line);
  transform-style: preserve-3d;
}

.hours-bar-wrapper {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  --bar: 50%;
  --tilt: 0deg;
  --delay: 0ms;
}

.hours-bar {
  position: relative;
  width: min(100%, 96px);
  flex: 1;
  min-height: 0;
  transform-style: preserve-3d;

  &::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: var(--bar);
    min-height: 6px;
    border-radius: 10px 10px 4px 4px;
    background: linear-gradient(180deg, rgb(158 203 255 / 0.95) 0%, rgb(109 180 255 / 0.5) 52%, rgb(61 141 255 / 0.1) 100%);
    border-top: 1px solid rgb(180 214 255 / 0.7);
    box-shadow:
      inset 0 1px 0 rgb(255 255 255 / 0.4),
      0 0 26px rgb(61 141 255 / 0.32),
      0 16px 30px -18px rgb(0 0 0 / 0.9);
    transform: rotateY(var(--tilt));
    transform-origin: 50% 100%;
    animation: bar-rise 600ms var(--transition-ease) both;
    animation-delay: var(--delay);
  }
}

.hours-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 56px;
  text-align: center;
}

.hours-label__short {
  font-size: 11px;
  letter-spacing: 0.05em;
  color: var(--color-ink-soft);
  text-transform: uppercase;
}

.hours-label__value {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 500;
  color: var(--color-accent);
}

.hours-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px solid var(--color-line);
}

.hours-total__label {
  font-size: 12px;
  color: var(--color-ink-soft);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.hours-total__value {
  font-family: var(--font-mono);
  font-size: 16px;
  font-weight: 600;
  color: var(--color-accent-strong);
}

@keyframes bar-rise {
  from {
    opacity: 0;
    transform: rotateY(var(--tilt)) scaleY(0);
  }
  to {
    opacity: 1;
    transform: rotateY(var(--tilt)) scaleY(1);
  }
}

/* Activities Module */
.activities-constellation {
  position: relative;
  width: 100%;
  height: 220px;
}

.activity-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  color: var(--color-ink-muted);
  font-size: 14px;
}

.activities-beam {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.activity-node {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 46%;
  font-size: 13px;

  --node-rgb: 109, 180, 255;

  &.node-pos-0 {
    top: 0;
    left: 0;
  }
  &.node-pos-1 {
    top: 0;
    right: 0;
  }
  &.node-pos-2 {
    bottom: 0;
    left: 0;
  }
  &.node-pos-3 {
    bottom: 0;
    right: 0;
  }

  animation: node-appear 400ms var(--transition-ease) forwards;
  animation-play-state: var(--node-anim, running);

  .is-reduced & {
    animation: none;
    opacity: 1;
  }
}

.node-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: rgb(var(--node-rgb));
  box-shadow: 0 0 12px rgb(var(--node-rgb) / 0.4);
  transition: box-shadow 300ms ease-out;

  .activity-node:hover & {
    box-shadow: 0 0 24px rgb(var(--node-rgb) / 0.8);
  }
}

.node-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.node-title {
  color: var(--color-ink);
  font-weight: 500;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.node-meta {
  font-size: 11px;
  color: var(--color-ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-ink-muted);
}

.node-foot span:last-child {
  color: rgb(var(--node-rgb));
}

.activity--effective .node-dot {
  --node-rgb: 79, 214, 196;
}

.activity--pending .node-dot {
  --node-rgb: 109, 180, 255;
}

.activity--refused .node-dot {
  --node-rgb: 255, 125, 146;
}

.activity--draft .node-dot {
  --node-rgb: 168, 139, 255;
}

@keyframes node-appear {
  from {
    opacity: 0;
    transform: scale(0.6);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

/* Extra Module */
.extra-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.extra-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  background: rgb(16 22 36 / 0.3);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-line);
  transition: all 200ms ease-out;

  &:hover {
    background: rgb(16 22 36 / 0.5);
    border-color: var(--color-line-strong);
  }
}

.extra-item__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  background: var(--color-accent);
}

.tone-blue .extra-item__dot {
  background: var(--tone-blue);
}

.tone-teal .extra-item__dot {
  background: var(--tone-teal);
}

.tone-violet .extra-item__dot {
  background: var(--tone-violet);
}

.tone-amber .extra-item__dot {
  background: var(--tone-amber);
}

.tone-rose .extra-item__dot {
  background: var(--tone-rose);
}

.tone-muted .extra-item__dot {
  background: var(--tone-muted);
}

.extra-item__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.extra-item__title {
  color: var(--color-ink);
  font-weight: 500;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extra-item__meta {
  color: var(--color-ink-soft);
  font-size: 12px;
}

.extra-item__detail {
  color: var(--color-ink-muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extra-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 120px;
  color: var(--color-ink-muted);
  font-size: 14px;
  text-align: center;
}

/* Navigation */
.stage__nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  padding: 24px;
  margin-bottom: 20px;
}

.nav-button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.03em;
  color: var(--color-ink);
  background: rgb(16 22 36 / 0.5);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 200ms ease-out;
  backdrop-filter: blur(8px);

  &:hover:not(:disabled) {
    background: rgb(16 22 36 / 0.8);
    border-color: var(--color-line-strong);
    color: var(--color-accent);
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  svg {
    width: 16px;
    height: 16px;
  }
}

.nav-dots {
  display: flex;
  align-items: center;
  gap: 12px;
}

.nav-dot {
  position: relative;
  width: 22px;
  height: 22px;
  padding: 0;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.nav-dot::before {
  content: '';
  position: absolute;
  inset: 7px;
  border-radius: 50%;
  background: rgb(142 178 255 / 0.3);
  border: 1px solid var(--color-line);
  transition:
    background-color 200ms ease-out,
    border-color 200ms ease-out,
    box-shadow 200ms ease-out;
}

.nav-dot.is-active::before {
  background: var(--color-accent);
  border-color: var(--color-accent-strong);
  box-shadow: 0 0 12px rgb(109 180 255 / 0.5);
}

.nav-dot:hover:not(.is-active)::before {
  background: rgb(142 178 255 / 0.55);
  border-color: var(--color-line-strong);
}

.nav-counter {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-ink-muted);
  letter-spacing: 0.05em;
  margin-left: 8px;
}

/* Responsive */
@media (max-width: 720px) {
  .stage {
    --step: 220px;
    padding-top: 76px;
  }

  .stage__card {
    max-width: calc(100vw - 32px);
    min-height: auto;
    padding: 32px 24px;
    gap: 24px;
  }

  .card-title {
    font-size: 20px;
  }

  .identity-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .hours-chart {
    height: 170px;
    gap: 10px;
  }

  .hours-bar {
    width: min(100%, 64px);
  }

  .activities-constellation {
    height: auto;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 16px;
  }

  .activities-beam {
    display: none;
  }

  .activity-node {
    position: static;
    max-width: none;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 12px;
    background: rgb(16 22 36 / 0.3);
    border: 1px solid var(--color-line);
    border-radius: var(--radius-sm);

    &.node-pos-0,
    &.node-pos-1,
    &.node-pos-2,
    &.node-pos-3 {
      position: static;
      top: auto;
      left: auto;
      right: auto;
      bottom: auto;
    }
  }

  .node-dot {
    width: 6px;
    height: 6px;
  }

  .stage__nav {
    gap: 16px;
    padding: 16px;
  }

  .nav-button {
    padding: 8px 12px;
    font-size: 12px;
    gap: 6px;

    svg {
      width: 14px;
      height: 14px;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .stage__card {
    animation: none;
  }

  .activity-node {
    animation: none;
    opacity: 1;
  }

  .hours-bar::before {
    animation: none;
  }
}
</style>

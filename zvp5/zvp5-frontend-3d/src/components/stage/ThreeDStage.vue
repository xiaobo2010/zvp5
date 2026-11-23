<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import StarfieldCanvas from './StarfieldCanvas.vue'

import type { StageActivity, StageData, StageExtraItem } from '@/types/stage'

const props = defineProps<{ data: StageData }>()

const active = ref(0)
const previousActive = ref(0)
const transitioning = ref(false)
const reduced = ref(false)
const warp = ref(false)
const reverse = ref(false)
const viewportWidth = ref(window.innerWidth)
const serverTime = ref(new Date(Date.now() + (props.data.serverOffsetMs ?? 0)))
const stageRoot = ref<HTMLElement | null>(null)
let transitionTimer: ReturnType<typeof setTimeout> | undefined
let serverTimeTimer: ReturnType<typeof setInterval> | undefined
let rafId: number | undefined
let targetX = 0
let targetY = 0
let currentX = 0
let currentY = 0

let media: MediaQueryList | null = null

const GAP = 900
const TRANSITION_DUR = 650

const modules = computed(() => props.data.modules)
const total = computed(() => modules.value.length)
const maxHour = computed(() =>
  Math.max(1, ...props.data.hours.points.map((p) => p.hours))
)
const recent = computed(() => props.data.activities.slice(0, 6))
const extraItems = computed(() => props.data.extra.items)
const agentItems = computed(() =>
  extraItems.value.filter((item) => item.kind === 'agent').slice(0, 5)
)
const rightItems = computed(() =>
  extraItems.value.filter((item) => item.kind !== 'agent').slice(0, 6)
)
const rightTitle = computed(() =>
  props.data.identity.isAdmin
    ? '风险日志'
    : props.data.identity.isDepartment
      ? '待审核'
      : '通知与动态'
)
const rightEmpty = computed(() =>
  props.data.identity.isAdmin
    ? '暂无风险日志'
    : props.data.identity.isDepartment
      ? '暂无待审核'
      : '暂无通知'
)
const isMobile = computed(() => viewportWidth.value <= 720)
const averageHours = computed(() =>
  props.data.hours.points.length > 0
    ? props.data.hours.total / props.data.hours.points.length
    : 0
)

function formatServerTime(): string {
  return serverTime.value.toLocaleTimeString('zh-CN', { hour12: false })
}

function formatServerDate(): string {
  return serverTime.value.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short'
  })
}

function getHourDelta(hours: number): string {
  const delta = hours - averageHours.value
  if (Math.abs(delta) < 0.05) return '与平均持平'
  const prefix = delta > 0 ? '比平均多' : '比平均少'
  return `${prefix} ${Math.abs(delta).toFixed(1)} h`
}

function tilt(position: 'left' | 'center' | 'right'): number {
  if (reduced.value) return 0
  if (position === 'center') return 0
  if (isMobile.value) return position === 'left' ? 6 : -6
  return position === 'left' ? 13 : -13
}

function depthStyle(index: number) {
  const raw = index - active.value
  const d = Math.max(-1, Math.min(1, raw))
  const z = -d * GAP + 0.01
  const scale = 1 - Math.abs(d) * 0.16
  const opacity = raw === 0 ? 1 : 0
  const isVisible = transitioning.value
    ? index === active.value || index === previousActive.value
    : index === active.value

  return {
    transform: `translate(-50%, -50%) translate3d(0, 0, ${z}px) scale(${scale})`,
    opacity: String(opacity),
    visibility: isVisible ? 'visible' : 'hidden',
    zIndex: String(100 - Math.abs(raw))
  } as const
}

function resetActiveScroll() {
  const id = modules.value[active.value]?.id
  if (!id) return
  const cluster = stageRoot.value?.querySelector<HTMLElement>(
    `.stage-module[data-module="${id}"] .cluster`
  )
  if (cluster) cluster.scrollTop = 0
}

function goTo(index: number) {
  if (index < 0 || index >= total.value || transitioning.value) return
  previousActive.value = active.value
  active.value = index
  reverse.value = index < previousActive.value
  transitioning.value = true
  warp.value = true
  stageRoot.value?.classList.add('is-transitioning')
  requestAnimationFrame(resetActiveScroll)

  if (transitionTimer) clearTimeout(transitionTimer)
  transitionTimer = setTimeout(() => {
    transitioning.value = false
    warp.value = false
    reverse.value = false
    stageRoot.value?.classList.remove('is-transitioning')
    // The incoming module's scroll container can be shifted by the Z transition;
    // put it back at the top once the scene has settled.
    requestAnimationFrame(resetActiveScroll)
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
  if (reduced.value || event.pointerType === 'touch') return
  targetX = (event.clientX / window.innerWidth - 0.5) * 2
  targetY = (event.clientY / window.innerHeight - 0.5) * 2
  startPointerLoop()
}

function startPointerLoop() {
  if (rafId !== undefined) return
  rafId = requestAnimationFrame(updatePointerFrame)
}

function updatePointerFrame() {
  const root = stageRoot.value
  if (!root) {
    rafId = undefined
    return
  }

  const lerpCoeff = 0.14
  currentX += (targetX - currentX) * lerpCoeff
  currentY += (targetY - currentY) * lerpCoeff

  const settled =
    Math.abs(targetX - currentX) < 0.001 &&
    Math.abs(targetY - currentY) < 0.001
  if (settled) {
    currentX = targetX
    currentY = targetY
  }

  // Mouse X drives rotateY, mouse Y drives rotateX.
  root.style.setProperty('--pointer-x', `${(currentY * 3.2).toFixed(2)}deg`)
  root.style.setProperty('--pointer-y', `${(currentX * 3.2).toFixed(2)}deg`)

  rafId = settled ? undefined : requestAnimationFrame(updatePointerFrame)
}

function syncMotion(e: MediaQueryListEvent | MediaQueryList) {
  reduced.value = e.matches
}

function onResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  syncMotion(media)
  media.addEventListener('change', syncMotion)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('resize', onResize)

  // Server time ticker with 1s interval
  serverTime.value = new Date(Date.now() + (props.data.serverOffsetMs ?? 0))
  serverTimeTimer = setInterval(() => {
    serverTime.value = new Date(Date.now() + (props.data.serverOffsetMs ?? 0))
  }, 1000)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', syncMotion)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('resize', onResize)
  if (transitionTimer) clearTimeout(transitionTimer)
  if (serverTimeTimer) clearInterval(serverTimeTimer)
  if (rafId !== undefined) cancelAnimationFrame(rafId)
})

// Seeded PRNG: stable per activity id, but irregular to the eye.
function seededRandom(seed: number): () => number {
  let state = seed >>> 0
  return function () {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0x100000000
  }
}

function activityScatterStyle(activity: StageActivity, index: number): Record<string, string> {
  if (isMobile.value) return {}

  // Hand-placed irregular anchors (percent of the 1040x500 field), then seeded
  // jitter. This is intentionally not a grid: the three top cards and three
  // lower cards sit at different heights and depths.
  const slots = [
    [4, 10],
    [28, 4],
    [55, 14],
    [12, 62],
    [46, 46],
    [73, 66]
  ]
  const visibleCount = Math.max(1, recent.value.length)
  const slotIndex =
    visibleCount > 1
      ? Math.round((index * (slots.length - 1)) / (visibleCount - 1))
      : 0
  const [baseX, baseY] = slots[slotIndex]

  let seed = 0
  for (let i = 0; i < activity.id.length; i++) {
    seed = (seed * 31 + activity.id.charCodeAt(i)) | 0
  }
  const rng = seededRandom(seed)

  const left = Math.min(74, Math.max(2, baseX + (rng() - 0.5) * 3.2))
  const top = Math.min(70, Math.max(2, baseY + (rng() - 0.5) * 4.5))
  const width = 22 + rng() * 2
  const height = 124 + Math.round(rng() * 14)
  const rotation = (rng() - 0.5) * 5
  const zDepth = -30 - Math.round(rng() * 120)

  // Tilt follows the actual x position, not the index.
  const center = left + width / 2
  const tilt = Math.max(-13, Math.min(13, ((center - 50) / 50) * 12))

  return {
    position: 'absolute',
    left: `${left.toFixed(2)}%`,
    top: `${top.toFixed(2)}%`,
    width: `${width.toFixed(2)}%`,
    height: `${height}px`,
    transform: `rotate(${rotation.toFixed(2)}deg)`,
    '--card-z': `${zDepth}px`,
    '--card-tilt': `${tilt.toFixed(1)}deg`,
    zIndex: String(1 + index)
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
  <div ref="stageRoot" class="stage" :class="{ 'is-reduced': reduced }">
    <!-- Background environment -->
    <div class="stage-backdrop">
      <div class="nebula nebula-1"></div>
      <div class="nebula nebula-2"></div>
      <div class="nebula nebula-3"></div>
      <div class="horizon-grid"></div>
    </div>

    <StarfieldCanvas :warp="warp" :reduced="reduced" :reverse="reverse" />
    <div class="stage-shade" aria-hidden="true"></div>

    <!-- 3D scene -->
    <div class="stage-scene">
      <div class="stage-track">
        <div
          v-for="(module, index) in modules"
          :key="module.id"
          class="stage-module"
          :data-module="module.id"
          :style="depthStyle(index)"
          :aria-hidden="index !== active ? 'true' : undefined"
        >
          <!-- Identity Module: 2-card layout -->
          <template v-if="module.id === 'identity'">
            <div class="cluster cluster--identity">
              <!-- Left: Greeting + Name + Info + Server Time -->
              <div
                class="card card--identity-left"
                :style="{ '--card-tilt': `${tilt('left')}deg` }"
              >
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

                <div class="identity-hero">
                  <p class="identity-greeting">{{ data.identity.greeting }}</p>
                  <h3 class="identity-name">{{ data.identity.name }}</h3>
                </div>

                <div class="identity-info">
                  <div class="info-row">
                    <span class="info-label">学号</span>
                    <span class="info-value">{{ data.identity.studentId }}</span>
                  </div>
                  <div class="info-row">
                    <span class="info-label">班级</span>
                    <span class="info-value">{{ data.identity.className }}</span>
                  </div>
                  <div class="info-row">
                    <span class="info-label">角色</span>
                    <span class="info-value">{{ data.identity.rolesLabel }}</span>
                  </div>
                  <div class="server-time">
                    <span class="info-label">服务器时间</span>
                    <strong class="server-clock">{{ formatServerTime() }}</strong>
                    <span class="server-date">{{ formatServerDate() }}</span>
                  </div>
                </div>

                <div class="card-reflection"></div>
              </div>

              <!-- Right: Total Hours + Pending Badge -->
              <div
                class="card card--identity-right"
                :style="{ '--card-tilt': `${tilt('right')}deg` }"
              >
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

                <div class="hours-display">
                  <span class="hours-label">累计时长</span>
                  <span class="hours-value">{{ data.identity.totalHours }}</span>
                  <span class="hours-unit">h</span>
                </div>

                <div v-if="data.identity.pendingCount > 0" class="pending-badge">
                  {{ data.identity.pendingCount }} 条待审批
                </div>

                <div class="card-reflection"></div>
              </div>
            </div>
          </template>

          <!-- Hours Module: 3-bar layout -->
          <template v-else-if="module.id === 'hours'">
            <div class="cluster cluster--hours">
              <div
                v-for="(point, idx) in data.hours.points"
                :key="point.key"
                class="hours-item"
                :style="{ '--card-tilt': `${tilt(idx === 0 ? 'left' : idx === 2 ? 'right' : 'center')}deg` }"
              >
                <div class="bar-head">
                  <span class="bar-label">{{ point.label }}</span>
                  <span class="bar-index">0{{ idx + 1 }}</span>
                </div>

                <div class="bar-stage">
                  <div class="bar-3d" :style="{ '--bar-height': point.hours / maxHour }">
                    <div class="bar-front"></div>
                  </div>
                </div>

                <div class="bar-display">
                  <div class="bar-value">{{ point.hours }}<span class="bar-unit">h</span></div>
                  <div class="bar-delta">{{ getHourDelta(point.hours) }}</div>
                </div>
              </div>
            </div>
          </template>

          <!-- Activities Module: irregular scatter field -->
          <template v-else-if="module.id === 'activities'">
            <div class="cluster cluster--activities">
              <div
                v-for="(activity, idx) in recent"
                :key="activity.id"
                class="activity-card-wrapper"
                :style="activityScatterStyle(activity, idx)"
              >
                <div
                  class="card card--activity"
                  :style="{ '--tone-rgb': activityTone(activity) }"
                >
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

                  <div class="card-reflection"></div>
                </div>
              </div>
            </div>
          </template>

          <!-- Extra Module: 2-card layout (agent + non-agent) -->
          <template v-else-if="module.id === 'extra'">
            <div class="cluster cluster--extra">
              <!-- Left: Agent items -->
              <div
                class="card card--extra-left"
                :style="{ '--card-tilt': `${tilt('left')}deg` }"
              >
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

                <h3 class="extra-title">ZVAgent 执行记录</h3>
                <div class="extra-list">
                  <div
                    v-for="item in agentItems"
                    :key="item.id"
                    class="extra-item"
                    :style="{ '--tone-rgb': extraTone(item) }"
                  >
                    <div class="extra-dot"></div>
                    <div class="extra-info">
                      <h4 class="extra-item-title">{{ item.title }}</h4>
                      <p class="extra-meta">{{ item.meta }}</p>
                      <p v-if="item.detail" class="extra-detail">{{ item.detail }}</p>
                    </div>
                  </div>
                </div>
                <div v-if="agentItems.length === 0" class="extra-empty">
                  暂无执行记录
                </div>

                <div class="card-reflection"></div>
              </div>

              <!-- Right: Non-agent items (by role) -->
              <div
                class="card card--extra-right"
                :style="{ '--card-tilt': `${tilt('right')}deg` }"
              >
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

                <h3 class="extra-title">{{ rightTitle }}</h3>
                <div class="extra-list">
                  <div
                    v-for="item in rightItems"
                    :key="item.id"
                    class="extra-item"
                    :style="{ '--tone-rgb': extraTone(item) }"
                  >
                    <div class="extra-dot"></div>
                    <div class="extra-info">
                      <h4 class="extra-item-title">{{ item.title }}</h4>
                      <p class="extra-meta">{{ item.meta }}</p>
                      <p v-if="item.detail" class="extra-detail">{{ item.detail }}</p>
                    </div>
                  </div>
                </div>
                <div v-if="rightItems.length === 0" class="extra-empty">
                  {{ rightEmpty }}
                </div>

                <div class="card-reflection"></div>
              </div>
            </div>
          </template>
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
  --transition-dur: 650ms;
  --transition-ease: cubic-bezier(0.16, 1, 0.3, 1);
  --pointer-x: 0deg;
  --pointer-y: 0deg;
}

.is-transitioning .stage-module {
  will-change: transform, opacity;
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

.stage-module {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 100%;
  transform-style: preserve-3d;
  transition:
    transform var(--transition-dur) var(--transition-ease),
    opacity var(--transition-dur) var(--transition-ease),
    visibility var(--transition-dur);
}

/* Cluster Container (multi-card layout per module) */
.cluster {
  position: relative;
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: center;
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
  padding: 0 20px;
  overflow-anchor: none;
}

.cluster--identity {
  gap: 32px;
  align-items: stretch;
}

.cluster--hours {
  gap: 28px;
}

.cluster--activities {
  position: relative;
  display: block;
  width: 100%;
  max-width: 1040px;
  height: 500px;
  padding: 0;
  overflow: visible;
}

.activity-card-wrapper {
  position: absolute;
  transform-style: preserve-3d;
}

.activity-card-wrapper .card {
  width: 100%;
  height: 100%;
  flex: none;
  min-height: 0;
}

.cluster--extra {
  gap: 32px;
}

/* Card Base */
.card {
  position: relative;
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  padding: 32px;
  background: linear-gradient(
    160deg,
    rgb(18 31 54 / 0.96),
    rgb(10 19 35 / 0.97)
  );
  border: 1px solid rgb(172 210 255 / 0.3);
  border-radius: 2px;
  transform-style: preserve-3d;
  transform: perspective(1000px) translateZ(var(--card-z, 0px))
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
  transition: none;
  box-shadow:
    0 22px 56px rgb(0 0 0 / 0.36),
    inset 0 1px 0 rgb(169 211 255 / 0.05);
}

.card:hover {
  transform: perspective(1000px) translateZ(calc(var(--card-z, 0px) + 6px))
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
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
    rgb(169 211 255 / 0.2),
    transparent
  );
  box-shadow: 0 0 20px rgb(169 211 255 / 0.15);
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
    rgb(169 211 255 / 0.015)
  );
  opacity: 0.18;
}

/* Identity Module Cards */
.card--identity-left,
.card--identity-right {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.identity-hero {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.identity-greeting {
  font-size: 13px;
  color: rgb(212 230 248 / 0.82);
  letter-spacing: 0.03em;
}

.identity-name {
  font-size: 36px;
  font-weight: 200;
  letter-spacing: -0.03em;
  color: #eef6ff;
}

.identity-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 20px;
  border-top: 1px solid rgb(169 211 255 / 0.08);
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 13px;
}

.info-label {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.15em;
  color: rgb(210 228 247 / 0.84);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  text-transform: uppercase;
}

.info-value {
  font-family: var(--font-mono);
  font-size: 14px;
  color: #c4e2ff;
  letter-spacing: 0.02em;
}

.info-value--mono {
  font-family: var(--font-mono);
  font-size: 15px;
}

.server-time {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px;
  background: rgb(16 24 42 / 0.3);
  border: 1px solid rgb(169 211 255 / 0.08);
  border-radius: 1px;
  margin-top: 6px;
}

.server-clock {
  font-family: var(--font-mono);
  font-size: 26px;
  font-weight: 300;
  letter-spacing: 0.04em;
  color: #eef6ff;
  line-height: 1;
}

.server-date {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: rgb(205 225 247 / 0.76);
  text-transform: uppercase;
}

.hours-display {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
}

.hours-label {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.18em;
  color: rgb(208 228 248 / 0.82);
  text-transform: uppercase;
}

.hours-value {
  font-size: 56px;
  font-weight: 200;
  color: #eef6ff;
  line-height: 1;
}

.hours-unit {
  font-size: 20px;
  font-weight: 300;
  color: rgb(205 225 247 / 0.82);
}

.pending-badge {
  display: inline-flex;
  align-items: center;
  padding: 6px 14px;
  font-size: 13px;
  letter-spacing: 0.02em;
  color: #ffc46b;
  background: rgb(255 196 107 / 0.08);
  border: 1px solid rgb(255 196 107 / 0.2);
  border-radius: 999px;
  margin: 0 auto;
}

/* Hours Module Cards */
.hours-item {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 24px;
  min-height: 380px;
  background: transparent;
  border: none;
  box-shadow: none;
  transform-style: preserve-3d;
  transform: perspective(1000px)
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
}

.bar-head {
  width: 100%;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.bar-index {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.16em;
  color: rgb(198 220 244 / 0.7);
}

.bar-stage {
  position: relative;
  width: 100%;
  flex: 1;
  min-height: 195px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding-bottom: 10px;
}

.bar-stage::before {
  content: '';
  position: absolute;
  left: 8%;
  right: 8%;
  bottom: 0;
  height: 82px;
  background-image:
    linear-gradient(rgb(169 211 255 / 0.14) 1px, transparent 1px),
    linear-gradient(90deg, rgb(169 211 255 / 0.12) 1px, transparent 1px);
  background-size: 28px 18px;
  transform: perspective(300px) rotateX(72deg);
  transform-origin: bottom;
  mask-image: linear-gradient(to top, #000 10%, transparent 90%);
  opacity: 0.55;
  pointer-events: none;
}

.bar-stage::after {
  content: '';
  position: absolute;
  left: 6%;
  right: 6%;
  bottom: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    rgb(169 211 255 / 0.28),
    transparent
  );
}

.bar-3d {
  position: relative;
  width: 46%;
  height: calc(var(--bar-height, 0.5) * 180px);
  min-height: 18px;
  transform-style: preserve-3d;
}

.bar-3d::before,
.bar-3d::after {
  content: '';
  position: absolute;
  box-sizing: border-box;
}

/* Isometric top cap: always visible from the front, unlike a horizontal 3D face. */
.bar-3d::before {
  top: -16px;
  left: 16px;
  right: 0;
  height: 16px;
  transform: skewX(-45deg);
  background: linear-gradient(180deg, rgb(169 211 255 / 0.32), rgb(109 180 255 / 0.12));
  border: 1px solid rgb(169 211 255 / 0.22);
}

/* Isometric right side, so the middle (untilted) bar still reads as a column. */
.bar-3d::after {
  top: -16px;
  right: -16px;
  bottom: 0;
  width: 16px;
  transform: skewY(-45deg);
  background: linear-gradient(90deg, rgb(41 86 150 / 0.75), rgb(18 34 60 / 0.92));
  border: 1px solid rgb(169 211 255 / 0.16);
}

.bar-front {
  position: absolute;
  inset: 0;
  box-sizing: border-box;
  background: linear-gradient(180deg, rgb(169 211 255 / 0.6), rgb(61 141 255 / 0.22));
  border: 1px solid rgb(169 211 255 / 0.35);
  box-shadow:
    inset 0 0 22px rgb(109 180 255 / 0.18),
    0 0 26px rgb(109 180 255 / 0.12);
}

.bar-display {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.bar-value {
  font-size: 44px;
  font-weight: 200;
  color: #d3ebff;
  line-height: 1;
  text-shadow: 0 2px 14px rgb(0 0 0 / 0.6);
}

.bar-unit {
  font-size: 16px;
  font-weight: 300;
  color: rgb(169 211 255 / 0.5);
  margin-left: 4px;
}

.bar-label {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.18em;
  color: rgb(208 228 248 / 0.85);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  text-transform: uppercase;
}

.bar-delta {
  font-family: var(--font-mono);
  font-size: 12px;
  color: rgb(208 228 248 / 0.85);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  letter-spacing: 0.02em;
  padding-top: 8px;
  border-top: 1px solid rgb(169 211 255 / 0.08);
  width: 100%;
  text-align: center;
}

/* Activities Module */
.card--activity {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.card--activity:hover {
  background: rgb(14 24 43 / 0.97);
  border-color: rgb(var(--tone-rgb) / 0.34);
  box-shadow: none;
}

.activity-status {
  position: absolute;
  top: 20px;
  right: 20px;
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
  padding-right: 20px;
}

.activity-meta {
  font-family: var(--font-mono);
  font-size: 13px;
  color: rgb(208 228 248 / 0.86);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
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
  color: #c8e5ff;
  letter-spacing: 0.05em;
}

/* Extra Module Cards */
.card--extra-left,
.card--extra-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow: hidden;
  max-height: 420px;
}

.card--extra-left .extra-title,
.card--extra-right .extra-title {
  flex-shrink: 0;
}

.extra-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

.extra-list::-webkit-scrollbar {
  width: 4px;
}

.extra-list::-webkit-scrollbar-track {
  background: rgb(16 24 42 / 0.3);
}

.extra-list::-webkit-scrollbar-thumb {
  background: rgb(169 211 255 / 0.2);
  border-radius: 2px;
}

.extra-title {
  font-size: 16px;
  font-weight: 400;
  color: #eef6ff;
  letter-spacing: -0.01em;
  padding-bottom: 12px;
  border-bottom: 1px solid rgb(169 211 255 / 0.08);
}

.extra-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.extra-item {
  display: flex;
  gap: 12px;
  padding: 12px;
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
  margin-top: 4px;
}

.extra-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.extra-item-title {
  font-size: 15px;
  font-weight: 400;
  color: #eef6ff;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extra-meta {
  font-family: var(--font-mono);
  font-size: 13px;
  color: rgb(208 228 248 / 0.86);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
  letter-spacing: 0.02em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extra-detail {
  font-size: 11px;
  color: rgb(198 220 244 / 0.8);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.extra-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 120px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: rgb(198 220 244 / 0.66);
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
  color: #c7e4ff;
  background: rgb(10 17 31 / 0.92);
  border: 1px solid rgb(169 211 255 / 0.22);
  border-radius: 1px;
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
  background: rgb(10 17 31 / 0.92);
  border: 1px solid rgb(169 211 255 / 0.22);
  border-radius: 1px;
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

  .cluster {
    max-height: calc(100dvh - 190px);
    overflow-y: auto;
    padding: 0 16px 8px;
  }

  .cluster--identity,
  .cluster--extra {
    flex-direction: column;
  }

  .cluster--hours {
    flex-direction: column;
    justify-content: flex-start;
    gap: 16px;
  }

  .cluster--activities {
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    gap: 14px;
    height: auto;
    max-height: calc(100dvh - 190px);
    overflow-y: auto;
    padding: 0 16px 8px;
  }

  .activity-card-wrapper {
    position: static;
    width: 100%;
    height: auto;
    transform: none;
    left: 0;
    top: 0;
  }

  .card {
    width: 100%;
    max-width: 100%;
    min-width: 0;
    padding: 24px;
    margin: 0 auto;
  }

  .card--identity-left,
  .card--identity-right {
    min-height: auto;
  }

  .identity-name {
    font-size: 28px;
  }

  .hours-display {
    padding: 32px 16px;
  }

  .hours-value {
    font-size: 44px;
  }

  .hours-item {
    flex: 0 0 auto;
    width: 100%;
    min-height: 0;
  }

  .bar-stage {
    min-height: 135px;
  }

  .bar-3d {
    width: 60%;
    height: calc(var(--bar-height, 0.5) * 115px);
  }

  .bar-value {
    font-size: 36px;
  }

  .server-clock {
    font-size: 22px;
  }

  .activity-card-wrapper .card {
    width: 100%;
    height: auto;
  }

  .card--activity {
    min-height: 120px;
  }

  .activity-title {
    font-size: 14px;
    padding-right: 16px;
  }

  .card--extra-left,
  .card--extra-right {
    max-height: 300px;
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
  .card,
  .card:hover,
  .hours-item,
  .activity-card-wrapper {
    transform: none !important;
  }

  .stage-track {
    transform: none !important;
  }

  .stage-module {
    transition: opacity var(--transition-dur) ease-out !important;
    transform: none !important;
    filter: none !important;
  }
}
</style>

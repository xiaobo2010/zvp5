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

const ZOOM_MIN = 0.72
const ZOOM_MAX = 1.35
const ZOOM_STEP = 0.06
const sceneScale = ref(1)

// Drag state
const draggingCard = ref<string | null>(null)
const cardOffsets = ref<Record<string, { x: number; y: number }>>({})
const cardStack = ref<Record<string, number>>({})
let cardStackSeq = 0
let dragStartX = 0
let dragStartY = 0
let dragStartOffsetX = 0
let dragStartOffsetY = 0

// Side navigation drag/swipe state
const SIDE_ZONE_RATIO = 0.28
let navDragging = false
let navStartX = 0
let navStartY = 0
let touchStartY = 0
let touchStartX = 0
let touchIsSide = false

function clampSceneScale(value: number) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 100) / 100))
}

function zoomBy(delta: number) {
  sceneScale.value = clampSceneScale(sceneScale.value + delta)
}

function resetZoom() {
  sceneScale.value = 1
}

function onWheelZoom(event: WheelEvent) {
  if (Math.abs(event.deltaY) < 1) return

  // Lists keep their native wheel scrolling. Everywhere else the wheel is
  // reserved for zooming the scene; it never switches modules.
  const target = event.target as HTMLElement
  const scrollable = target.closest<HTMLElement>('.extra-list, .cluster')
  if (scrollable && scrollable.scrollHeight > scrollable.clientHeight + 1) {
    return
  }

  event.preventDefault()
  zoomBy(event.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP)
}

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

const DESKTOP_TILT_MAX = 18
const MOBILE_TILT_MAX = 10
const CLUSTER_WIDTH = 900
const ACTIVITY_WIDTH = 1040

function tiltFromCenter(centerPercent: number): number {
  if (reduced.value) return 0
  const maxTilt = isMobile.value ? MOBILE_TILT_MAX : DESKTOP_TILT_MAX
  // 25%/75% are the extreme side-card centers; centered cards stay flat.
  const normalized = (centerPercent - 50) / 25
  return Math.max(-maxTilt, Math.min(maxTilt, normalized * maxTilt))
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
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    event.preventDefault()
    go(1)
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
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

  // Increased from ±3.2° to ±9°. Horizontal rotation is intentionally
  // mirrored: moving to the left edge should turn the scene to the right.
  root.style.setProperty('--pointer-x', `${(currentY * 9).toFixed(2)}deg`)
  root.style.setProperty('--pointer-y', `${(-currentX * 9).toFixed(2)}deg`)

  rafId = settled ? undefined : requestAnimationFrame(updatePointerFrame)
}

function syncMotion(e: MediaQueryListEvent | MediaQueryList) {
  reduced.value = e.matches
}

function onResize() {
  viewportWidth.value = window.innerWidth
}

function isSideZone(x: number): boolean {
  const edge = Math.max(96, window.innerWidth * SIDE_ZONE_RATIO)
  return x <= edge || x >= window.innerWidth - edge
}

function isInteractiveTarget(target: HTMLElement): boolean {
  return Boolean(
    target.closest(
      'button, a, input, textarea, select, .card, .hours-item, .activity-card-wrapper, .extra-list, .cluster, .instrument-dock, .stage-nav, .zoom-hud, .drag-hint'
    )
  )
}

// Mouse/pen horizontal drag on the left or right side. Cards keep their own
// drag gesture; the centre of the screen stays free for interacting with cards.
function onStagePointerDown(event: PointerEvent) {
  if (event.pointerType === 'touch' || event.button !== 0) return
  if (!isSideZone(event.clientX)) return
  if (isInteractiveTarget(event.target as HTMLElement)) return

  navDragging = true
  navStartX = event.clientX
  navStartY = event.clientY
  event.preventDefault()
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'ew-resize'
  window.addEventListener('pointermove', onStagePointerMove)
  window.addEventListener('pointerup', onStagePointerEnd)
  window.addEventListener('pointercancel', onStagePointerEnd)
}

function onStagePointerMove(event: PointerEvent) {
  if (!navDragging) return
  if (Math.abs(event.clientX - navStartX) > 4) event.preventDefault()
}

function onStagePointerEnd(event: PointerEvent) {
  if (!navDragging) return
  navDragging = false
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  window.removeEventListener('pointermove', onStagePointerMove)
  window.removeEventListener('pointerup', onStagePointerEnd)
  window.removeEventListener('pointercancel', onStagePointerEnd)

  const dx = event.clientX - navStartX
  const dy = event.clientY - navStartY
  if (Math.abs(dx) > 64 && Math.abs(dx) > Math.abs(dy) * 1.25) {
    go(dx < 0 ? 1 : -1)
  }
}

// Dragging logic (desktop only)
function onCardPointerDown(event: PointerEvent, cardKey: string) {
  if (isMobile.value) return
  const target = event.target as HTMLElement
  // Don't start drag from scrollable .extra-list content
  if (target.closest('.extra-list')) return

  event.preventDefault()
  draggingCard.value = cardKey
  cardStack.value[cardKey] = ++cardStackSeq
  dragStartX = event.clientX
  dragStartY = event.clientY
  const current = cardOffsets.value[cardKey] || { x: 0, y: 0 }
  dragStartOffsetX = current.x
  dragStartOffsetY = current.y

  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'grabbing'

  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragEnd)
}

function onDragMove(event: PointerEvent) {
  if (!draggingCard.value) return
  const dx = event.clientX - dragStartX
  const dy = event.clientY - dragStartY

  // Keep a generous viewport bound instead of a 40%-of-screen cage. The old
  // clamp made cards stop at an invisible line near the centre of the stage.
  const maxX = Math.max(320, window.innerWidth * 0.8)
  const maxY = Math.max(220, window.innerHeight * 0.55)

  cardOffsets.value[draggingCard.value] = {
    x: Math.max(-maxX, Math.min(maxX, dragStartOffsetX + dx)),
    y: Math.max(-maxY, Math.min(maxY, dragStartOffsetY + dy))
  }
}

function onDragEnd() {
  if (!draggingCard.value) return
  draggingCard.value = null
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
}

function getCardStyle(cardKey: string, baseCenter: number) {
  const offset = cardOffsets.value[cardKey] || { x: 0, y: 0 }
  const actualCenter = baseCenter + (offset.x / CLUSTER_WIDTH) * 100
  const actualTilt = tiltFromCenter(actualCenter)

  return {
    '--drag-x': `${offset.x}px`,
    '--drag-y': `${offset.y}px`,
    '--card-tilt': `${actualTilt.toFixed(1)}deg`,
    cursor: isMobile.value ? 'default' : (draggingCard.value === cardKey ? 'grabbing' : 'grab'),
    zIndex: draggingCard.value === cardKey ? 1000 : cardStack.value[cardKey]
  }
}

// Side swipe navigation (touch). Vertical gestures inside lists stay native.
function onTouchStart(event: TouchEvent) {
  if (draggingCard.value || event.touches.length !== 1) return
  const touch = event.touches[0]
  const target = event.target as HTMLElement
  touchIsSide =
    isSideZone(touch.clientX) &&
    !target.closest('button, a, input, textarea, select, .instrument-dock, .stage-nav, .zoom-hud')
  touchStartY = touch.clientY
  touchStartX = touch.clientX
}

function onTouchEnd(event: TouchEvent) {
  if (!touchIsSide || draggingCard.value) {
    touchIsSide = false
    return
  }
  touchIsSide = false
  const touch = event.changedTouches[0]
  const deltaY = touch.clientY - touchStartY
  const deltaX = touch.clientX - touchStartX

  // Horizontal swipe threshold: ~56px, more horizontal than vertical.
  if (Math.abs(deltaX) > 56 && Math.abs(deltaX) > Math.abs(deltaY) * 1.25) {
    go(deltaX < 0 ? 1 : -1) // swipe left = next, swipe right = previous
  }
}

onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  syncMotion(media)
  media.addEventListener('change', syncMotion)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('resize', onResize)
  window.addEventListener('touchstart', onTouchStart, { passive: true })
  window.addEventListener('touchend', onTouchEnd, { passive: true })

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
  window.removeEventListener('touchstart', onTouchStart)
  window.removeEventListener('touchend', onTouchEnd)
  if (transitionTimer) clearTimeout(transitionTimer)
  if (serverTimeTimer) clearInterval(serverTimeTimer)
  if (rafId !== undefined) cancelAnimationFrame(rafId)
  if (navDragging) {
    navDragging = false
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    window.removeEventListener('pointermove', onStagePointerMove)
    window.removeEventListener('pointerup', onStagePointerEnd)
    window.removeEventListener('pointercancel', onStagePointerEnd)
  }
  onDragEnd()
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
  const cardKey = `activity-${activity.id}`
  const offset = cardOffsets.value[cardKey] || { x: 0, y: 0 }

  return {
    position: 'absolute',
    left: `${left.toFixed(2)}%`,
    top: `${top.toFixed(2)}%`,
    width: `${width.toFixed(2)}%`,
    height: `${height}px`,
    '--drag-x': `${offset.x}px`,
    '--drag-y': `${offset.y}px`,
    '--card-rotate': `${rotation.toFixed(2)}deg`,
    '--card-scale': `${(1 + zDepth / 1000).toFixed(3)}`,
    cursor: isMobile.value ? 'default' : draggingCard.value === cardKey ? 'grabbing' : 'grab',
    zIndex: String(
      draggingCard.value === cardKey ? 1000 : (cardStack.value[cardKey] ?? 1 + index)
    )
  }
}

function getActivityCardStyle(activity: StageActivity, index: number) {
  const cardKey = `activity-${activity.id}`
  const offset = cardOffsets.value[cardKey] || { x: 0, y: 0 }

  // Base tilt from seeded position
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
  const [baseX] = slots[slotIndex]

  let seed = 0
  for (let i = 0; i < activity.id.length; i++) {
    seed = (seed * 31 + activity.id.charCodeAt(i)) | 0
  }
  const rng = seededRandom(seed)
  const left = Math.min(74, Math.max(2, baseX + (rng() - 0.5) * 3.2))
  const width = 22 + rng() * 2

  // Compute tilt from the card's actual center, including its drag offset.
  const actualCenter = left + width / 2 + (offset.x / ACTIVITY_WIDTH) * 100
  const actualTilt = tiltFromCenter(actualCenter)

  return {
    '--drag-x': '0px',
    '--drag-y': '0px',
    '--card-z': '0px',
    '--card-tilt': `${actualTilt.toFixed(1)}deg`
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

    <!-- Left instrument dock -->
    <nav class="instrument-dock" aria-label="主导航">
      <div class="dock-head" aria-hidden="true">
        <span class="dock-head-label">NAV</span>
        <span class="dock-head-line"></span>
        <span class="dock-head-code">03</span>
      </div>
      <button
        type="button"
        class="dock-btn is-active"
        @click="goTo(0)"
        aria-label="首页"
        aria-current="page"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M3 10L10 3L17 10M5 8V17H8V13H12V17H15V8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="dock-label">首页</span>
        <span class="dock-led"></span>
      </button>

      <button
        type="button"
        class="dock-btn"
        disabled
        aria-label="义工列表（待开放）"
        aria-disabled="true"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M3 6H17M3 10H17M3 14H17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span class="dock-label">义工列表</span>
        <span class="dock-status">待接入</span>
      </button>

      <button
        type="button"
        class="dock-btn"
        disabled
        aria-label="个人中心（待开放）"
        aria-disabled="true"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <circle cx="10" cy="7" r="3" stroke="currentColor" stroke-width="1.5"/>
          <path d="M4 17C4 14 6.5 12 10 12C13.5 12 16 14 16 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span class="dock-label">个人中心</span>
        <span class="dock-status">待开放</span>
      </button>
    </nav>

    <!-- 3D scene -->
    <div class="stage-scene" @wheel="onWheelZoom" @pointerdown="onStagePointerDown">
      <div class="scene-zoom" :style="{ '--scene-scale': sceneScale }">
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
                :data-card-key="`identity-left`"
                :style="getCardStyle('identity-left', 24.1)"
                @pointerdown="onCardPointerDown($event, 'identity-left')"
              >
                <div class="card-thickness" aria-hidden="true">
                  <span class="card-thickness__top"></span>
                  <span class="card-thickness__bottom"></span>
                </div>
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
                :data-card-key="`identity-right`"
                :style="getCardStyle('identity-right', 75.9)"
                @pointerdown="onCardPointerDown($event, 'identity-right')"
              >
                <div class="card-thickness" aria-hidden="true">
                  <span class="card-thickness__top"></span>
                  <span class="card-thickness__bottom"></span>
                </div>
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
                :data-card-key="`hours-${idx}`"
                :style="getCardStyle(`hours-${idx}`, [15.6, 50, 84.4][idx] ?? 50)"
                @pointerdown="onCardPointerDown($event, `hours-${idx}`)"
              >
                <div class="bar-head">
                  <span class="bar-label">{{ point.label }}</span>
                  <span class="bar-index">0{{ idx + 1 }}</span>
                </div>

                <div class="bar-stage">
                  <div class="bar-3d" :style="{ '--bar-height': point.hours / maxHour }">
                    <div class="bar-face bar-face--front"></div>
                    <div class="bar-face bar-face--back"></div>
                    <div class="bar-face bar-face--left"></div>
                    <div class="bar-face bar-face--right"></div>
                    <div class="bar-face bar-face--top"></div>
                    <div class="bar-face bar-face--bottom"></div>
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
                :data-card-key="`activity-${activity.id}`"
                :style="activityScatterStyle(activity, idx)"
                @pointerdown="onCardPointerDown($event, `activity-${activity.id}`)"
              >
                <div
                  class="card card--activity"
                  :style="{ '--tone-rgb': activityTone(activity), ...getActivityCardStyle(activity, idx) }"
                >
                  <div class="card-thickness" aria-hidden="true">
                    <span class="card-thickness__top"></span>
                    <span class="card-thickness__bottom"></span>
                  </div>
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
                :data-card-key="`extra-left`"
                :style="getCardStyle('extra-left', 24.1)"
                @pointerdown="onCardPointerDown($event, 'extra-left')"
              >
                <div class="card-thickness" aria-hidden="true">
                  <span class="card-thickness__top"></span>
                  <span class="card-thickness__bottom"></span>
                </div>
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
                :data-card-key="`extra-right`"
                :style="getCardStyle('extra-right', 75.9)"
                @pointerdown="onCardPointerDown($event, 'extra-right')"
              >
                <div class="card-thickness" aria-hidden="true">
                  <span class="card-thickness__top"></span>
                  <span class="card-thickness__bottom"></span>
                </div>
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
    </div>

    <!-- Navigation: depth rail only -->
    <div class="stage-nav">
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
        <div class="rail-hint">两侧滑动 · 滚轮缩放</div>
      </div>

      <div
        class="nav-announcement sr-only"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ transitioning ? '' : `当前模块: ${modules[active]?.title}` }}
      </div>
    </div>

    <!-- Scene zoom -->
    <div class="zoom-hud" role="group" aria-label="场景缩放">
      <button
        type="button"
        class="zoom-btn"
        :disabled="sceneScale <= ZOOM_MIN + 0.001"
        @click="zoomBy(-ZOOM_STEP)"
        aria-label="缩小场景"
      >−</button>
      <button
        type="button"
        class="zoom-value"
        @click="resetZoom"
        aria-label="重置缩放"
        title="重置缩放"
      >{{ Math.round(sceneScale * 100) }}%</button>
      <button
        type="button"
        class="zoom-btn"
        :disabled="sceneScale >= ZOOM_MAX - 0.001"
        @click="zoomBy(ZOOM_STEP)"
        aria-label="放大场景"
      >+</button>
    </div>

    <!-- Drag hint -->
    <div class="drag-hint" v-if="!isMobile">卡片可拖动</div>
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

/* Instrument Dock */
.instrument-dock {
  position: fixed;
  left: 20px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 110;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 10px;
  background: linear-gradient(160deg, rgb(10 17 31 / 0.95), rgb(6 11 22 / 0.97));
  border: 1px solid rgb(111 215 196 / 0.3);
  clip-path: polygon(0 8px, 8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%);
  box-shadow:
    0 0 20px rgb(111 215 196 / 0.1),
    inset 0 1px 0 rgb(111 215 196 / 0.08);
}

.instrument-dock::before {
  content: '';
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgb(111 215 196 / 0.015) 2px,
    rgb(111 215 196 / 0.015) 4px
  );
  pointer-events: none;
}

.instrument-dock::after {
  content: '';
  position: absolute;
  left: 4px;
  top: 16px;
  bottom: 16px;
  width: 1px;
  background: repeating-linear-gradient(
    0deg,
    rgb(111 215 196 / 0.4) 0 1px,
    transparent 1px 8px
  );
  pointer-events: none;
}

.dock-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px 6px;
  font-family: var(--font-mono);
  font-size: 8px;
  letter-spacing: 0.16em;
  color: rgb(111 215 196 / 0.55);
  text-transform: uppercase;
}

.dock-head-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, rgb(111 215 196 / 0.34), transparent);
}

.dock-head-code {
  color: rgb(169 211 255 / 0.45);
}

.dock-btn {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  min-width: 140px;
  clip-path: polygon(0 0, calc(100% - 6px) 0, 100% 6px, 100% 100%, 0 100%);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: rgb(169 211 255 / 0.75);
  background: rgb(14 22 38 / 0.4);
  border: 1px solid rgb(111 215 196 / 0.15);
  border-radius: 2px;
  cursor: pointer;
  transition: all 200ms ease-out;
  text-align: left;
}

.dock-btn:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.dock-btn:not(:disabled):hover {
  background: rgb(14 22 38 / 0.7);
  border-color: rgb(111 215 196 / 0.35);
  color: #c4e2ff;
  box-shadow: 0 0 16px rgb(111 215 196 / 0.15);
}

.dock-btn.is-active {
  background: rgb(14 22 38 / 0.8);
  border-color: rgb(111 215 196 / 0.6);
  color: #eef6ff;
  box-shadow:
    0 0 20px rgb(111 215 196 / 0.25),
    inset 0 0 12px rgb(111 215 196 / 0.08);
}

.dock-icon {
  flex-shrink: 0;
  color: currentColor;
}

.dock-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
}

.dock-led {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #6fd7c4;
  box-shadow: 0 0 8px rgb(111 215 196 / 0.8);
}

.dock-status {
  font-size: 9px;
  color: rgb(169 211 255 / 0.4);
  letter-spacing: 0.06em;
}

/* 3D Scene */
.stage-scene {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-height: 100vh;
  padding: 72px 0 104px;
  padding-left: 190px;
  display: flex;
  align-items: center;
  justify-content: center;
  perspective: 1400px;
  z-index: 10;
}

.scene-zoom {
  width: 100%;
  height: 100%;
  transform-style: preserve-3d;
  transform: scale(var(--scene-scale, 1));
  transition: transform 180ms ease-out;
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

.stage-module[aria-hidden='true'] {
  pointer-events: none;
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
  transform: translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    rotate(var(--card-rotate, 0deg)) scale(var(--card-scale, 1));
  transition: transform 100ms ease-out;
  background: rgb(0 0 0 / 0.001);
}

.activity-card-wrapper .card {
  width: 100%;
  height: 100%;
  flex: none;
  min-height: 0;
  /* Keep the card hit-testable; pointerdown bubbles to the draggable wrapper. */
  pointer-events: auto;
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
  --card-depth: 8px;
  transform-style: preserve-3d;
  transform: perspective(1000px)
    translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    translateZ(var(--card-z, 0px))
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
  transition: transform 100ms ease-out;
  box-shadow:
    4px 4px 0 -1px rgb(8 15 28 / 0.98),
    5px 5px 0 -1px rgb(52 96 150 / 0.32),
    0 22px 56px rgb(0 0 0 / 0.36),
    inset 0 1px 0 rgb(169 211 255 / 0.05);
}

.card:hover {
  transform: perspective(1000px)
    translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    translateZ(calc(var(--card-z, 0px) + 6px))
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
}

.card-thickness {
  position: absolute;
  inset: 0;
  transform: translateZ(calc(var(--card-depth, 8px) * -1));
  transform-style: preserve-3d;
  background: linear-gradient(160deg, rgb(10 18 33 / 0.98), rgb(5 10 20 / 0.99));
  border: 1px solid rgb(120 170 235 / 0.2);
  border-radius: 2px;
  pointer-events: none;
}

.card-thickness::before,
.card-thickness::after,
.card-thickness__top,
.card-thickness__bottom {
  position: absolute;
  box-sizing: border-box;
  background: linear-gradient(180deg, rgb(34 63 102 / 0.96), rgb(12 24 44 / 0.98));
  border: 1px solid rgb(150 200 255 / 0.24);
}

.card-thickness::before,
.card-thickness::after {
  content: '';
  top: 0;
  width: var(--card-depth, 8px);
  height: 100%;
}

.card-thickness::before {
  right: 0;
  transform-origin: 100% 50%;
  transform: translateZ(calc(var(--card-depth, 8px) * -1)) rotateY(90deg);
}

.card-thickness::after {
  left: 0;
  transform-origin: 0 50%;
  transform: translateZ(calc(var(--card-depth, 8px) * -1)) rotateY(-90deg);
  background: linear-gradient(270deg, rgb(34 63 102 / 0.96), rgb(12 24 44 / 0.98));
}

.card-thickness__top {
  top: 0;
  left: 0;
  width: 100%;
  height: var(--card-depth, 8px);
  transform-origin: 50% 0;
  transform: rotateX(-90deg);
}

.card-thickness__bottom {
  bottom: 0;
  left: 0;
  width: 100%;
  height: var(--card-depth, 8px);
  transform-origin: 50% 100%;
  transform: rotateX(90deg);
  background: linear-gradient(180deg, rgb(14 27 48 / 0.98), rgb(6 12 24 / 0.99));
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
    translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    rotateY(calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)))
    rotateX(var(--pointer-x, 0deg));
  transition: transform 100ms ease-out;
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
  --bar-depth: 22px;
  position: relative;
  width: 46%;
  height: calc(var(--bar-height, 0.5) * 180px);
  min-height: 18px;
  transform-style: preserve-3d;
  transform-origin: 50% 100%;
  transform: rotateX(-4deg) rotateY(calc(-16deg - var(--card-tilt, 0deg)));
}

.bar-face {
  position: absolute;
  box-sizing: border-box;
  border: 1px solid rgb(169 211 255 / 0.22);
  pointer-events: none;
}

.bar-face--front {
  inset: 0;
  transform: translateZ(0);
  background: linear-gradient(180deg, rgb(169 211 255 / 0.55), rgb(52 130 245 / 0.22));
  box-shadow:
    inset 0 0 22px rgb(109 180 255 / 0.18),
    0 0 26px rgb(109 180 255 / 0.12);
}

.bar-face--back {
  inset: 0;
  transform: translateZ(calc(var(--bar-depth) * -1));
  background: rgb(8 16 30 / 0.96);
  border-color: rgb(120 170 235 / 0.16);
}

.bar-face--left,
.bar-face--right {
  top: 0;
  width: var(--bar-depth);
  height: 100%;
  background: linear-gradient(90deg, rgb(58 112 178 / 0.95), rgb(18 38 70 / 0.98));
}

.bar-face--right {
  right: 0;
  transform-origin: 100% 50%;
  transform: translateZ(calc(var(--bar-depth) * -1)) rotateY(90deg);
}

.bar-face--left {
  left: 0;
  transform-origin: 0 50%;
  transform: translateZ(calc(var(--bar-depth) * -1)) rotateY(-90deg);
  background: linear-gradient(270deg, rgb(58 112 178 / 0.92), rgb(18 38 70 / 0.98));
}

.bar-face--top {
  top: 0;
  left: 0;
  width: 100%;
  height: var(--bar-depth);
  transform-origin: 50% 0;
  transform: rotateX(-90deg);
  background: linear-gradient(180deg, rgb(169 211 255 / 0.5), rgb(92 160 245 / 0.26));
  border-color: rgb(169 211 255 / 0.32);
}

.bar-face--bottom {
  bottom: 0;
  left: 0;
  width: 100%;
  height: var(--bar-depth);
  transform-origin: 50% 100%;
  transform: rotateX(90deg);
  background: rgb(8 16 30 / 0.96);
  border-color: rgb(120 170 235 / 0.14);
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
  overflow: visible;
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

/* Scene zoom HUD */
.zoom-hud {
  position: fixed;
  right: 26px;
  bottom: 28px;
  z-index: 120;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  background: rgb(10 17 31 / 0.92);
  border: 1px solid rgb(169 211 255 / 0.22);
  border-radius: 1px;
  font-family: var(--font-mono);
}

.zoom-btn,
.zoom-value {
  height: 28px;
  padding: 0;
  color: #c7e4ff;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 1px;
  cursor: pointer;
  transition: all 160ms ease-out;
}

.zoom-btn {
  width: 28px;
  font-size: 16px;
  line-height: 1;
}

.zoom-value {
  min-width: 48px;
  font-size: 11px;
  letter-spacing: 0.04em;
}

.zoom-btn:hover:not(:disabled),
.zoom-value:hover {
  background: rgb(169 211 255 / 0.08);
  border-color: rgb(169 211 255 / 0.2);
  color: #eef6ff;
}

.zoom-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
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

.rail-hint {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  color: rgb(169 211 255 / 0.4);
  margin-top: 4px;
}

.drag-hint {
  position: fixed;
  right: 26px;
  bottom: 68px;
  z-index: 120;
  padding: 6px 12px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  color: rgb(169 211 255 / 0.5);
  background: rgb(10 17 31 / 0.85);
  border: 1px solid rgb(169 211 255 / 0.15);
  border-radius: 1px;
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
    padding-left: 60px;
  }

  .instrument-dock {
    left: 8px;
    padding: 8px 6px;
    gap: 6px;
  }

  .dock-head {
    display: none;
  }

  .instrument-dock::after {
    display: none;
  }

  .dock-btn {
    min-width: 0;
    padding: 10px;
    gap: 0;
  }

  .dock-label,
  .dock-status {
    display: none;
  }

  .dock-icon {
    width: 18px;
    height: 18px;
  }

  .dock-led {
    top: 6px;
    right: 6px;
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
    --card-depth: 5px;
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
    --bar-depth: 15px;
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

  .depth-rail {
    padding: 12px 16px;
  }

  .rail-track {
    width: 120px;
  }

  .rail-hint {
    font-size: 8px;
  }

  .zoom-hud {
    right: 14px;
    bottom: 118px;
  }

  .zoom-btn {
    width: 26px;
    height: 26px;
  }

  .zoom-value {
    min-width: 44px;
    height: 26px;
    font-size: 10px;
  }

  .drag-hint {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .card,
  .card:hover,
  .hours-item {
    transform: perspective(1000px)
      translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0) !important;
  }

  .activity-card-wrapper {
    transform: translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0) !important;
  }

  .stage-track {
    transform: none !important;
  }

  .scene-zoom {
    transition: none;
  }

  .stage-module {
    transition: opacity var(--transition-dur) ease-out !important;
    transform: none !important;
    filter: none !important;
  }
}
</style>

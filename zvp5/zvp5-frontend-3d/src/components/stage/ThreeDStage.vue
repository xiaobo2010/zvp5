<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import StarfieldCanvas from './StarfieldCanvas.vue'
import CardBrackets from './CardBrackets.vue'
import {
  changePassword,
  clearSession,
  fetchActivities,
  fetchMyDevice,
  logout as logoutSession,
  readableError,
  unbindPrimaryDevice,
  validatePasswordStrength,
  verifyPassword
} from '@/api/canary'
import { mapActivity } from '@/views/buildStageData'

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

type StageView = 'home' | 'volunteers' | 'profile'
const view = ref<StageView>('home')

const profileActive = ref(0)
const profilePreviousActive = ref(0)
const profileTransitioning = ref(false)
const profileGroups = [
  {
    id: 'account',
    kicker: '个人中心 01 / 03',
    title: '用户名与身份',
    summary: '账号基础信息'
  },
  {
    id: 'security',
    kicker: '个人中心 02 / 03',
    title: '账号安全与设备',
    summary: '修改密码、解绑学海设备'
  },
  {
    id: 'preferences',
    kicker: '个人中心 03 / 03',
    title: '外观与会话',
    summary: '切换主题、退出登录'
  }
] as const

const gyroSupported = ref(false)
const gyroState = ref<'unsupported' | 'prompt' | 'active' | 'denied'>('unsupported')
const gyroError = ref('')
let gyroListening = false
let gyroBaselinePitch: number | null = null
let gyroBaselineRoll: number | null = null
let gyroBaselineYaw: number | null = null
let gyroYawSmooth = 0
let screenOrientationAngle = 0

const VOLUNTEER_PAGE_SIZE = 8
const volunteerItems = ref<StageActivity[]>([])
const volunteerPage = ref(1)
const volunteerTotal = ref(0)
const volunteerBusy = ref(false)
const volunteerError = ref('')

const primaryDeviceId = ref<string | null>(null)
const deviceBusy = ref(false)
const deviceError = ref('')
const actionMessage = ref('')

const passwordDialog = ref<HTMLDialogElement | null>(null)
const deviceDialog = ref<HTMLDialogElement | null>(null)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordError = ref('')
const passwordBusy = ref(false)
const unbindPassword = ref('')

// Drag state
const draggingCard = ref<string | null>(null)
const cardOffsets = ref<Record<string, { x: number; y: number }>>({})
const cardStack = ref<Record<string, number>>({})
let cardStackSeq = 0
let dragStartX = 0
let dragStartY = 0
let dragStartOffsetX = 0
let dragStartOffsetY = 0
let dragPointerType = 'mouse'
let dragMoved = false

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
  const scrollable = target.closest<HTMLElement>('.extra-list, .cluster, .page-track')
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

const DESKTOP_TILT_MAX = 30
const MOBILE_TILT_MAX = 30
const POINTER_YAW_MAX = 35
const POINTER_PITCH_MAX = 5
const CLUSTER_WIDTH = 900
const ACTIVITY_WIDTH = 1040

function tiltFromCenter(centerPercent: number): number {
  if (reduced.value) return 0
  const maxTilt = isMobile.value ? MOBILE_TILT_MAX : DESKTOP_TILT_MAX
  // 25%/75% are the extreme side-card centers; centered cards stay flat.
  // At rest the left card leans toward the centre and the right card mirrors
  // it. Pointer movement then rotates both cards left/right together.
  const normalized = (centerPercent - 50) / 25
  return Math.max(-maxTilt, Math.min(maxTilt, -normalized * maxTilt))
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

function profileDepthStyle(index: number) {
  const raw = index - profileActive.value
  const d = Math.max(-1, Math.min(1, raw))
  const z = -d * GAP + 0.01
  const scale = 1 - Math.abs(d) * 0.16
  const isVisible = profileTransitioning.value
    ? index === profileActive.value || index === profilePreviousActive.value
    : index === profileActive.value

  return {
    transform: `translate(-50%, -50%) translate3d(0, 0, ${z}px) scale(${scale})`,
    opacity: String(raw === 0 ? 1 : 0),
    visibility: isVisible ? 'visible' : 'hidden',
    zIndex: String(100 - Math.abs(raw))
  } as const
}

const railItems = computed(() =>
  view.value === 'profile'
    ? profileGroups.map((group) => ({ id: group.id, title: group.title }))
    : modules.value
)
const railActive = computed(() =>
  view.value === 'profile' ? profileActive.value : active.value
)
const railTotal = computed(() => railItems.value.length)

function goProfile(index: number) {
  if (view.value !== 'profile') return
  if (index < 0 || index >= profileGroups.length || profileTransitioning.value) return
  profilePreviousActive.value = profileActive.value
  profileActive.value = index
  reverse.value = index < profilePreviousActive.value
  profileTransitioning.value = true
  warp.value = true
  stageRoot.value?.classList.add('is-transitioning')
  requestAnimationFrame(resetProfileScroll)

  if (transitionTimer) clearTimeout(transitionTimer)
  transitionTimer = setTimeout(() => {
    profileTransitioning.value = false
    warp.value = false
    reverse.value = false
    stageRoot.value?.classList.remove('is-transitioning')
    requestAnimationFrame(resetProfileScroll)
  }, TRANSITION_DUR)
}

function resetProfileScroll() {
  const id = profileGroups[profileActive.value]?.id
  if (!id) return
  const cluster = stageRoot.value?.querySelector<HTMLElement>(
    `.stage-module[data-profile="${id}"] .cluster`
  )
  if (cluster) cluster.scrollTop = 0
}

function goRail(index: number) {
  if (view.value === 'profile') goProfile(index)
  else goTo(index)
}

function goTo(index: number) {
  if (view.value !== 'home') return
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

const volunteerTotalPages = computed(() =>
  Math.max(1, Math.ceil(volunteerTotal.value / VOLUNTEER_PAGE_SIZE))
)
const visibleVolunteerPages = computed(() => {
  const totalPages = volunteerTotalPages.value
  const start = Math.max(
    1,
    Math.min(volunteerPage.value - 2, totalPages - 4)
  )
  const end = Math.min(totalPages, start + 4)
  return Array.from({ length: end - start + 1 }, (_, offset) => start + offset)
})

function showHome() {
  view.value = 'home'
  goTo(0)
}

async function showVolunteers(page = 1) {
  view.value = 'volunteers'
  await loadVolunteerPage(page)
}

async function loadVolunteerPage(page: number) {
  const next = Math.max(1, Math.min(volunteerTotalPages.value, page))
  volunteerBusy.value = true
  volunteerError.value = ''
  try {
    const result = await fetchActivities('mine', next, VOLUNTEER_PAGE_SIZE, '')
    volunteerItems.value = result.activities.map(mapActivity)
    volunteerTotal.value = result.total
    volunteerPage.value = next
  } catch (reason) {
    volunteerError.value = readableError(reason, '义工记录加载失败，请稍后重试。')
  } finally {
    volunteerBusy.value = false
  }
}

async function showProfile() {
  const wasProfile = view.value === 'profile'
  view.value = 'profile'
  if (!wasProfile) {
    profileActive.value = 0
    profilePreviousActive.value = 0
    profileTransitioning.value = false
  }
  actionMessage.value = ''
  deviceBusy.value = true
  deviceError.value = ''
  try {
    const result = await fetchMyDevice()
    primaryDeviceId.value = result.primary_xuehai_id || null
  } catch (reason) {
    deviceError.value = readableError(reason, '设备信息加载失败。')
  } finally {
    deviceBusy.value = false
  }
}

function openPasswordDialog() {
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  passwordError.value = ''
  passwordDialog.value?.showModal()
}

function closePasswordDialog() {
  if (passwordBusy.value) return
  passwordDialog.value?.close()
}

async function submitPassword() {
  if (passwordBusy.value) return
  passwordError.value = ''
  if (!validatePasswordStrength(newPassword.value)) {
    passwordError.value = '新密码需为 8–14 位可打印字符，且至少包含三类字符。'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = '两次输入的新密码不一致。'
    return
  }
  passwordBusy.value = true
  try {
    const token = await verifyPassword(
      props.data.identity.studentId,
      currentPassword.value
    )
    await changePassword(props.data.identity.userOid, newPassword.value, token)
    clearSession()
    passwordDialog.value?.close()
    window.location.assign('/#/login')
  } catch (reason) {
    passwordError.value = readableError(reason, '密码修改失败，请稍后重试。')
  } finally {
    passwordBusy.value = false
  }
}

function openDeviceDialog() {
  if (!primaryDeviceId.value) {
    actionMessage.value = '当前账号还未绑定主学海设备。'
    return
  }
  unbindPassword.value = ''
  deviceError.value = ''
  deviceDialog.value?.showModal()
}

function closeDeviceDialog() {
  if (deviceBusy.value) return
  deviceDialog.value?.close()
}

async function submitUnbind() {
  if (deviceBusy.value || !primaryDeviceId.value) return
  deviceError.value = ''
  deviceBusy.value = true
  try {
    const token = await verifyPassword(
      props.data.identity.studentId,
      unbindPassword.value
    )
    await unbindPrimaryDevice(token)
    primaryDeviceId.value = null
    deviceDialog.value?.close()
    actionMessage.value = '主学海设备已解绑。'
  } catch (reason) {
    deviceError.value = readableError(reason, '解绑失败，请稍后重试。')
  } finally {
    deviceBusy.value = false
  }
}

function openThemePicker() {
  window.location.assign('/?choose-theme')
}

async function handleLogout() {
  await logoutSession()
  window.location.assign('/#/login')
}

function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) {
    return
  }
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    event.preventDefault()
    navigateHorizontal(1)
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    event.preventDefault()
    navigateHorizontal(-1)
  }
}

function navigateHorizontal(delta: number) {
  if (view.value === 'profile') goProfile(profileActive.value + delta)
  else if (view.value === 'home') go(delta)
}

function onPointerMove(event: PointerEvent) {
  if (reduced.value) return
  // Touch devices have no hover state: use the finger position as the
  // parallax source while it is moving, unless that pointer is dragging a card.
  if (event.pointerType === 'touch' && draggingCard.value) return
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

  // Pointer rotation follows the horizontal axis: moving left leans cards
  // left, moving right leans them right. The horizontal range is the full
  // 35° the cards are allowed to turn; the per-card base lean is clamped
  // against the same ceiling in CSS so no card ever exceeds it.
  root.style.setProperty('--pointer-x', `${(currentY * POINTER_PITCH_MAX).toFixed(2)}deg`)
  root.style.setProperty('--pointer-y', `${(currentX * POINTER_YAW_MAX).toFixed(2)}deg`)

  rafId = settled ? undefined : requestAnimationFrame(updatePointerFrame)
}

function syncMotion(e: MediaQueryListEvent | MediaQueryList) {
  reduced.value = e.matches
}

function onResize() {
  viewportWidth.value = window.innerWidth
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function normalizeAngleDiff(a: number, b: number): number {
  let diff = a - b
  while (diff > 180) diff -= 360
  while (diff < -180) diff += 360
  return diff
}

function computeScreenOrientedAngles(
  alpha: number,
  beta: number,
  gamma: number,
  screenAngle: number
): { pitch: number; roll: number; yaw: number } {
  // Build device→world rotation matrix: R = Rz(α) · Rx(β) · Ry(γ)
  const a = (alpha * Math.PI) / 180
  const b = (beta * Math.PI) / 180
  const g = (gamma * Math.PI) / 180

  const ca = Math.cos(a), sa = Math.sin(a)
  const cb = Math.cos(b), sb = Math.sin(b)
  const cg = Math.cos(g), sg = Math.sin(g)

  const r11 = ca * cg - sa * sb * sg
  const r12 = -sa * cb
  const r13 = ca * sg + sa * sb * cg
  const r21 = sa * cg + ca * sb * sg
  const r22 = ca * cb
  const r23 = sa * sg - ca * sb * cg
  const r31 = -cb * sg
  const r32 = sb
  const r33 = cb * cg

  // Screen axes in the device frame. `screen.orientation.angle` is the
  // clockwise rotation of the document inside the device, so the content's
  // right/up axes are rotated by -angle in device coordinates:
  //   sr_dev = (c, -s, 0), su_dev = (s, c, 0), normal = device z (0, 0, 1)
  const theta = (screenAngle * Math.PI) / 180
  const c = Math.cos(theta), s = Math.sin(theta)

  const srWorldZ = r31 * c + r32 * -s
  const suWorldZ = r31 * s + r32 * c

  // Pitch: elevation of the screen normal above the horizon. Device roll
  // rotates around the normal, so it cannot leak into this value.
  const pitch = Math.asin(clampNumber(r33, -1, 1))

  // Roll: signed in-plane rotation of world-up around the viewing axis.
  // atan2 is scale invariant, so pitch does not leak into this value either.
  // Positive roll = the content's right edge points up (device rolled CCW).
  const roll = Math.atan2(srWorldZ, suWorldZ)

  return {
    pitch: (pitch * 180) / Math.PI,
    roll: (roll * 180) / Math.PI,
    // Yaw: the tablet heading about the world vertical. `alpha` is a
    // compass-style heading and (unlike the screen normal) stays defined
    // while the tablet is held at an angle, so turning the whole tablet
    // in/out around its midline is visible on the side cards.
    yaw: alpha
  }
}

function onDeviceOrientation(event: DeviceOrientationEvent) {
  if (!gyroSupported.value) return
  if (event.alpha === null || event.beta === null || event.gamma === null) return
  gyroState.value = 'active'

  const angles = computeScreenOrientedAngles(
    event.alpha,
    event.beta,
    event.gamma,
    screenOrientationAngle
  )

  // Set baseline on first read or recalibration
  if (gyroBaselinePitch === null) gyroBaselinePitch = angles.pitch
  if (gyroBaselineRoll === null) gyroBaselineRoll = angles.roll
  if (gyroBaselineYaw === null) gyroBaselineYaw = angles.yaw

  // Compute deltas (normalized for ±180 wrap)
  const pitchDelta = normalizeAngleDiff(angles.pitch, gyroBaselinePitch)
  const rollDelta = normalizeAngleDiff(angles.roll, gyroBaselineRoll)
  const yawDelta = normalizeAngleDiff(angles.yaw, gyroBaselineYaw)

  // Keep the movement subtle: the screen is only a window onto the scene.
  const clampedPitch = clampNumber(pitchDelta * 0.55, -9, 9)
  const clampedRoll = clampNumber(rollDelta * 0.65, -9, 9)
  // Yaw is low-passed: compass headings are noisier than the gravity vector.
  // No hard ceiling here: the inward/outward tablet turn is the strongest 3D
  // cue, and the old ±12° cap flattened it. The gain is 2.5× the previous one,
  // toned down to 60% (0.7 × 2.5 × 0.6 = 1.05) after device feedback.
  const targetYaw = yawDelta * 0.7 * 2.5 * 0.6
  gyroYawSmooth += (targetYaw - gyroYawSmooth) * 0.25

  const root = stageRoot.value
  if (!root) return

  // The content counter-rotates against the device so the cards read as
  // world-upright: a forward pitch (top edge away) pulls the card bottom
  // inward, and a device roll is cancelled by an equal on-screen roll.
  root.style.setProperty('--gyro-x', `${(-clampedPitch).toFixed(2)}deg`)
  root.style.setProperty('--gyro-z', `${clampedRoll.toFixed(2)}deg`)
  root.style.setProperty('--gyro-y', `${(-gyroYawSmooth).toFixed(2)}deg`)
}

function startGyroListener() {
  if (gyroListening) return
  gyroListening = true
  
  // Update screen orientation angle
  updateScreenOrientation()
  
  window.addEventListener('deviceorientation', onDeviceOrientation)
  
  // Track orientation changes (modern API and fallback)
  if (screen.orientation) {
    screen.orientation.addEventListener('change', onOrientationChange)
  } else {
    window.addEventListener('orientationchange', onOrientationChange)
  }
}

function updateScreenOrientation() {
  if (screen.orientation && screen.orientation.angle !== undefined) {
    screenOrientationAngle = screen.orientation.angle
  } else if (window.orientation !== undefined) {
    screenOrientationAngle = window.orientation
  } else {
    screenOrientationAngle = 0
  }
}

function onOrientationChange() {
  updateScreenOrientation()
  // Reset baseline to recalibrate and clear current angles
  gyroBaselinePitch = null
  gyroBaselineRoll = null
  gyroBaselineYaw = null
  gyroYawSmooth = 0

  const root = stageRoot.value
  if (root) {
    root.style.setProperty('--gyro-x', '0deg')
    root.style.setProperty('--gyro-z', '0deg')
    root.style.setProperty('--gyro-y', '0deg')
  }
}

async function enableGyro() {
  gyroError.value = ''
  const orientation = window.DeviceOrientationEvent as unknown as {
    requestPermission?: () => Promise<string>
  }
  try {
    if (typeof orientation?.requestPermission === 'function') {
      const permission = await orientation.requestPermission()
      if (permission !== 'granted') {
        gyroState.value = 'denied'
        gyroError.value = '陀螺仪权限未授予。'
        return
      }
    }
    startGyroListener()
    gyroState.value = 'active'
  } catch {
    gyroState.value = 'denied'
    gyroError.value = '当前设备无法启用陀螺仪。'
  }
}

function setupGyro() {
  const orientation = window.DeviceOrientationEvent as unknown as {
    requestPermission?: () => Promise<string>
  }
  const touchCapable =
    navigator.maxTouchPoints > 0 || 'ontouchstart' in window
  gyroSupported.value = Boolean(orientation) && touchCapable
  if (!gyroSupported.value) {
    gyroState.value = 'unsupported'
    return
  }
  if (typeof orientation.requestPermission === 'function') {
    gyroState.value = 'prompt'
    return
  }
  startGyroListener()
  gyroState.value = 'active'
}

function resetViewport() {
  const root = stageRoot.value
  if (!root) return

  // Reset gyro baseline (recapture current orientation as neutral)
  gyroBaselinePitch = null
  gyroBaselineRoll = null
  gyroBaselineYaw = null
  gyroYawSmooth = 0

  // Instantly reset all CSS variables (no transition animation)
  root.style.setProperty('--gyro-x', '0deg')
  root.style.setProperty('--gyro-z', '0deg')
  root.style.setProperty('--gyro-y', '0deg')
  root.style.setProperty('--pointer-x', '0deg')
  root.style.setProperty('--pointer-y', '0deg')

  // Reset pointer tracking
  targetX = 0
  targetY = 0
  currentX = 0
  currentY = 0

  // Reset zoom
  sceneScale.value = 1

  // Snap every dragged card back to its layout position
  cardOffsets.value = {}
  draggingCard.value = null

  // Show feedback
  actionMessage.value = '视角与卡片已复位'
  setTimeout(() => {
    actionMessage.value = ''
  }, 2200)
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
  if (view.value !== 'home' && view.value !== 'profile') return
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
    navigateHorizontal(dx < 0 ? 1 : -1)
  }
}

// Dragging logic (mouse, pen and touch). Page lists keep vertical scrolling.
function onCardPointerDown(event: PointerEvent, cardKey: string) {
  const target = event.target as HTMLElement
  // Don't start drag from scrollable content or an action inside a card.
  if (target.closest('.extra-list, button, a, input, textarea, select')) return

  if (event.pointerType !== 'touch') event.preventDefault()
  draggingCard.value = cardKey
  cardStack.value[cardKey] = ++cardStackSeq
  dragStartX = event.clientX
  dragStartY = event.clientY
  dragPointerType = event.pointerType
  dragMoved = false
  const current = cardOffsets.value[cardKey] || { x: 0, y: 0 }
  dragStartOffsetX = current.x
  dragStartOffsetY = current.y

  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'grabbing'

  window.addEventListener('pointermove', onDragMove, { passive: false })
  window.addEventListener('pointerup', onDragEnd)
  window.addEventListener('pointercancel', onDragEnd)
}

function onDragMove(event: PointerEvent) {
  if (!draggingCard.value) return
  const dx = event.clientX - dragStartX
  const dy = event.clientY - dragStartY

  if (dragPointerType === 'touch') {
    if (!dragMoved && Math.hypot(dx, dy) < 6) return
    // A mostly-vertical gesture in a scrollable page stays a page scroll.
    const target = event.target instanceof Element ? event.target : null
    if (!dragMoved && Math.abs(dy) > Math.abs(dx) * 1.15 && target?.closest('.page-track')) {
      onDragEnd()
      return
    }
    dragMoved = true
    if (Math.abs(dx) > Math.abs(dy)) event.preventDefault()
  }

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
  dragPointerType = 'mouse'
  dragMoved = false
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
  window.removeEventListener('pointercancel', onDragEnd)
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
  if (view.value !== 'home' && view.value !== 'profile') {
    touchIsSide = false
    return
  }
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
    navigateHorizontal(deltaX < 0 ? 1 : -1) // swipe left = next, swipe right = previous
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
  setupGyro()

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
  if (gyroListening) {
    gyroListening = false
    window.removeEventListener('deviceorientation', onDeviceOrientation)
  }
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

const VOLUNTEER_WIDTH = 1040
const VOLUNTEER_SLOTS = [
  [2, 8],
  [26, 2],
  [50, 10],
  [74, 5],
  [4, 52],
  [28, 48],
  [52, 58],
  [76, 54]
]

function volunteerScatterStyle(activity: StageActivity, index: number): Record<string, string> {
  if (isMobile.value) return {}
  const cardKey = `volunteer-${activity.id}`
  const offset = cardOffsets.value[cardKey] || { x: 0, y: 0 }
  const slot = VOLUNTEER_SLOTS[index % VOLUNTEER_SLOTS.length]
  const [baseX, baseY] = slot

  let seed = 0
  for (let i = 0; i < activity.id.length; i++) {
    seed = (seed * 31 + activity.id.charCodeAt(i)) | 0
  }
  const rng = seededRandom(seed)
  const left = Math.min(74, Math.max(2, baseX + (rng() - 0.5) * 2.2))
  const top = Math.min(66, Math.max(2, baseY + (rng() - 0.5) * 3.2))
  const width = 21.5
  const height = 136

  return {
    position: 'absolute',
    left: `${left.toFixed(2)}%`,
    top: `${top.toFixed(2)}%`,
    width: `${width}%`,
    height: `${height}px`,
    '--drag-x': `${offset.x}px`,
    '--drag-y': `${offset.y}px`,
    '--card-rotate': `${((rng() - 0.5) * 4).toFixed(2)}deg`,
    '--card-scale': `${(1 - (index % 3) * 0.015).toFixed(3)}`,
    cursor: isMobile.value ? 'default' : draggingCard.value === cardKey ? 'grabbing' : 'grab',
    zIndex: String(
      draggingCard.value === cardKey ? 1000 : (cardStack.value[cardKey] ?? 1 + index)
    )
  }
}

function getVolunteerCardStyle(activity: StageActivity, index: number) {
  const cardKey = `volunteer-${activity.id}`
  const offset = cardOffsets.value[cardKey] || { x: 0, y: 0 }
  const slot = VOLUNTEER_SLOTS[index % VOLUNTEER_SLOTS.length]
  const [baseX] = slot
  const width = 21.5
  const actualCenter = baseX + width / 2 + (offset.x / VOLUNTEER_WIDTH) * 100

  return {
    '--drag-x': '0px',
    '--drag-y': '0px',
    '--card-z': '0px',
    '--card-tilt': `${tiltFromCenter(actualCenter).toFixed(1)}deg`
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
  <div
    ref="stageRoot"
    class="stage"
    :class="{
      'is-reduced': reduced,
      'is-card-dragging': Boolean(draggingCard),
      'is-volunteers': view === 'volunteers'
    }"
    @wheel="onWheelZoom"
  >
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
        class="dock-btn"
        :class="{ 'is-active': view === 'home' }"
        @click="showHome"
        aria-label="首页"
        :aria-current="view === 'home' ? 'page' : undefined"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M3 10L10 3L17 10M5 8V17H8V13H12V17H15V8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="dock-label">首页</span>
        <span v-if="view === 'home'" class="dock-led"></span>
      </button>

      <button
        type="button"
        class="dock-btn"
        :class="{ 'is-active': view === 'volunteers' }"
        @click="showVolunteers(1)"
        aria-label="义工列表"
        :aria-current="view === 'volunteers' ? 'page' : undefined"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M3 6H17M3 10H17M3 14H17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span class="dock-label">义工列表</span>
        <span v-if="view === 'volunteers'" class="dock-led"></span>
      </button>

      <button
        type="button"
        class="dock-btn"
        :class="{ 'is-active': view === 'profile' }"
        @click="showProfile"
        aria-label="个人中心"
        :aria-current="view === 'profile' ? 'page' : undefined"
      >
        <svg class="dock-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <circle cx="10" cy="7" r="3" stroke="currentColor" stroke-width="1.5"/>
          <path d="M4 17C4 14 6.5 12 10 12C13.5 12 16 14 16 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span class="dock-label">个人中心</span>
        <span v-if="view === 'profile'" class="dock-led"></span>
      </button>
    </nav>

    <!-- 3D scene -->
    <div class="stage-scene" @pointerdown="onStagePointerDown">
      <div class="scene-zoom" :style="{ '--scene-scale': sceneScale }">
        <div v-if="view === 'home'" class="stage-track">
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
                <CardBrackets />

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

              </div>

              <!-- Right: Total Hours + Pending Badge -->
              <div
                class="card card--identity-right"
                :data-card-key="`identity-right`"
                :style="getCardStyle('identity-right', 75.9)"
                @pointerdown="onCardPointerDown($event, 'identity-right')"
              >
                <CardBrackets />

                <div class="hours-display">
                  <span class="hours-label">累计时长</span>
                  <span class="hours-value">{{ data.identity.totalHours }}</span>
                  <span class="hours-unit">h</span>
                </div>

                <div v-if="data.identity.pendingCount > 0" class="pending-badge">
                  {{ data.identity.pendingCount }} 条待审批
                </div>

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
                <CardBrackets />
                <div class="bar-head">
                  <span class="bar-label">{{ point.label }}</span>
                  <span class="bar-index">0{{ idx + 1 }}</span>
                </div>

                <div class="bar-stage">
                  <div
                    class="bar-2d"
                    :style="{ height: `${Math.max(6, Math.round((point.hours / maxHour) * 155))}px` }"
                  >
                    <span class="bar-2d__cap"></span>
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
                <CardBrackets />

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
                <CardBrackets />

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

              </div>

              <!-- Right: Non-agent items (by role) -->
              <div
                class="card card--extra-right"
                :data-card-key="`extra-right`"
                :style="getCardStyle('extra-right', 75.9)"
                @pointerdown="onCardPointerDown($event, 'extra-right')"
              >
                <CardBrackets />

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

              </div>
            </div>
          </template>
        </div>
        </div>

        <!-- Volunteer list: 8 scattered cards per page -->
        <div v-else-if="view === 'volunteers'" class="stage-track page-track page-track--volunteers">
          <div class="page-heading">
            <span class="page-kicker">02 / 记录</span>
            <h2 class="page-title">我的义工列表</h2>
            <p class="page-summary">仅显示当前账号自己的记录 · 每页最多 8 条</p>
          </div>

          <div v-if="volunteerBusy" class="page-state">正在读取义工记录…</div>
          <div v-else-if="volunteerError" class="page-state page-state--error">
            {{ volunteerError }}
          </div>
          <div v-else-if="volunteerItems.length === 0" class="page-state">
            还没有义工记录。
          </div>
          <div v-else class="cluster cluster--volunteer">
            <div
              v-for="(activity, idx) in volunteerItems"
              :key="activity.id"
              class="activity-card-wrapper volunteer-card-wrapper"
              :data-card-key="`volunteer-${activity.id}`"
              :style="volunteerScatterStyle(activity, idx)"
              @pointerdown="onCardPointerDown($event, `volunteer-${activity.id}`)"
            >
              <div
                class="card card--activity card--volunteer"
                :style="{ '--tone-rgb': activityTone(activity), ...getVolunteerCardStyle(activity, idx) }"
              >
                <CardBrackets />
                <div class="activity-status"></div>
                <h4 class="activity-title">{{ activity.name }}</h4>
                <div class="activity-meta">
                  <span>{{ activity.dateLabel }}</span>
                  <span class="meta-sep">·</span>
                  <span>{{ activity.place }}</span>
                </div>
                <div class="activity-type">{{ activity.typeLabel }} · {{ activity.statusLabel }}</div>
                <div class="activity-hours">
                  {{ activity.hours !== null ? `${activity.hours}h` : '待记录' }}
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- Personal center: three depth modules, same transition as home -->
        <div v-else class="stage-track">
          <div
            v-for="(group, index) in profileGroups"
            :key="group.id"
            class="stage-module profile-module"
            :data-profile="group.id"
            :style="profileDepthStyle(index)"
            :aria-hidden="index !== profileActive ? 'true' : undefined"
          >
            <div class="profile-group">
              <div class="profile-group__heading">
                <span class="page-kicker">{{ group.kicker }}</span>
                <h2 class="page-title">{{ group.title }}</h2>
                <p class="page-summary">{{ group.summary }}</p>
              </div>

              <div class="cluster cluster--profile">
                <div
                  v-if="group.id === 'account'"
                  class="card card--profile"
                  :data-card-key="'profile-name'"
                  :style="getCardStyle('profile-name', 25)"
                  @pointerdown="onCardPointerDown($event, 'profile-name')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">用户名</span>
                  <strong class="profile-card__value">{{ data.identity.name }}</strong>
                  <span class="profile-card__meta">学号 {{ data.identity.studentId }}</span>
                </div>

                <div
                  v-if="group.id === 'account'"
                  class="card card--profile"
                  :data-card-key="'profile-identity'"
                  :style="getCardStyle('profile-identity', 75)"
                  @pointerdown="onCardPointerDown($event, 'profile-identity')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">身份</span>
                  <strong class="profile-card__value">{{ data.identity.rolesLabel }}</strong>
                  <span class="profile-card__meta">{{ data.identity.className }}</span>
                </div>

                <div
                  v-if="group.id === 'security'"
                  class="card card--profile card--profile-action"
                  :data-card-key="'profile-password'"
                  :style="getCardStyle('profile-password', 25)"
                  @pointerdown="onCardPointerDown($event, 'profile-password')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">账号安全</span>
                  <strong class="profile-card__value">修改密码</strong>
                  <span class="profile-card__meta">验证当前密码后设置新密码</span>
                  <button class="profile-card__action" type="button" @click.stop="openPasswordDialog">
                    修改密码
                  </button>
                </div>

                <div
                  v-if="group.id === 'security'"
                  class="card card--profile card--profile-action"
                  :data-card-key="'profile-device'"
                  :style="getCardStyle('profile-device', 75)"
                  @pointerdown="onCardPointerDown($event, 'profile-device')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">学海设备</span>
                  <strong class="profile-card__value">
                    {{ deviceBusy ? '读取中…' : (primaryDeviceId ? '已绑定' : '未绑定') }}
                  </strong>
                  <span class="profile-card__meta">
                    {{ primaryDeviceId ? `主设备 ${primaryDeviceId}` : '当前账号没有主学海设备' }}
                  </span>
                  <button
                    class="profile-card__action"
                    type="button"
                    :disabled="deviceBusy || !primaryDeviceId"
                    @click.stop="openDeviceDialog"
                  >
                    解绑设备
                  </button>
                  <div v-if="deviceError" class="profile-card__error">{{ deviceError }}</div>
                </div>

                <div
                  v-if="group.id === 'preferences'"
                  class="card card--profile card--profile-action"
                  :data-card-key="'profile-theme'"
                  :style="getCardStyle('profile-theme', 25)"
                  @pointerdown="onCardPointerDown($event, 'profile-theme')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">外观</span>
                  <strong class="profile-card__value">切换主题</strong>
                  <span class="profile-card__meta">前往站点主题选择器</span>
                  <button class="profile-card__action" type="button" @click.stop="openThemePicker">
                    打开主题选择
                  </button>
                </div>

                <div
                  v-if="group.id === 'preferences'"
                  class="card card--profile card--profile-action"
                  :data-card-key="'profile-logout'"
                  :style="getCardStyle('profile-logout', 75)"
                  @pointerdown="onCardPointerDown($event, 'profile-logout')"
                >
                  <CardBrackets />
                  <span class="profile-card__label">会话</span>
                  <strong class="profile-card__value">退出登录</strong>
                  <span class="profile-card__meta">清除当前设备的登录状态</span>
                  <button class="profile-card__action profile-card__action--danger" type="button" @click.stop="handleLogout">
                    退出登录
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="view === 'volunteers' && volunteerTotalPages > 1"
      class="volunteer-pager"
      aria-label="义工记录分页"
    >
      <button
        type="button"
        class="pager-btn"
        :disabled="volunteerPage <= 1 || volunteerBusy"
        @click="loadVolunteerPage(volunteerPage - 1)"
      >上一页</button>
      <button
        v-for="page in visibleVolunteerPages"
        :key="page"
        type="button"
        class="pager-btn pager-btn--number"
        :class="{ 'is-active': page === volunteerPage }"
        :aria-current="page === volunteerPage ? 'page' : undefined"
        :disabled="volunteerBusy"
        @click="loadVolunteerPage(page)"
      >{{ page }}</button>
      <button
        type="button"
        class="pager-btn"
        :disabled="volunteerPage >= volunteerTotalPages || volunteerBusy"
        @click="loadVolunteerPage(volunteerPage + 1)"
      >下一页</button>
      <span class="pager-count">{{ volunteerPage }} / {{ volunteerTotalPages }}</span>
    </div>

    <!-- Navigation: depth rail only -->
    <div v-if="view === 'home' || view === 'profile'" class="stage-nav">
      <div class="depth-rail">
        <div class="rail-track">
          <div class="rail-progress" :style="{ width: `${(railTotal > 1 ? railActive / (railTotal - 1) : 0) * 100}%` }"></div>
          <div class="rail-ticks">
            <button
              v-for="(item, idx) in railItems"
              :key="item.id"
              type="button"
              class="rail-tick"
              :class="{ 'is-active': idx === railActive, 'is-past': idx < railActive }"
              @click="goRail(idx)"
              :aria-label="`转到${item.title}`"
              :aria-current="idx === railActive ? 'page' : undefined"
            ></button>
          </div>
        </div>
        <div class="rail-counter">
          <span class="counter-current">{{ String(railActive + 1).padStart(2, '0') }}</span>
          <span class="counter-sep">/</span>
          <span class="counter-total">{{ String(railTotal).padStart(2, '0') }}</span>
        </div>
        <div class="rail-hint">两侧滑动 · 滚轮缩放</div>
      </div>

      <div
        class="nav-announcement sr-only"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ (view === 'profile' ? profileTransitioning : transitioning) ? '' : `当前模块: ${railItems[railActive]?.title}` }}
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

    <div v-if="gyroSupported" class="gyro-hud">
      <button
        type="button"
        class="gyro-reset"
        @click="resetViewport"
        aria-label="复位视角"
        title="复位视角"
      >复位</button>
      <button
        v-if="gyroState === 'prompt' || gyroState === 'denied'"
        type="button"
        class="gyro-toggle"
        :disabled="gyroState === 'denied'"
        @click="enableGyro"
      >
        {{ gyroState === 'denied' ? '陀螺仪不可用' : '启用陀螺仪' }}
      </button>
      <span v-else class="gyro-chip">陀螺仪</span>
      <span v-if="gyroError" class="gyro-error">{{ gyroError }}</span>
    </div>

    <!-- Drag hint -->
    <div class="drag-hint" v-if="!isMobile">卡片可拖动</div>

    <div v-if="actionMessage" class="stage-toast" role="status">{{ actionMessage }}</div>

    <dialog ref="passwordDialog" class="stage-dialog" aria-labelledby="stage-password-title">
      <form class="stage-dialog__form" @submit.prevent="submitPassword">
        <p class="stage-dialog__kicker">账号安全</p>
        <h2 id="stage-password-title" class="stage-dialog__title">修改密码</h2>
        <p class="stage-dialog__hint">需要先验证当前密码，再设置 8–14 位新密码。</p>
        <label class="stage-dialog__field">
          <span>当前密码</span>
          <input v-model="currentPassword" type="password" autocomplete="current-password" required />
        </label>
        <label class="stage-dialog__field">
          <span>新密码</span>
          <input v-model="newPassword" type="password" autocomplete="new-password" required />
        </label>
        <label class="stage-dialog__field">
          <span>确认新密码</span>
          <input v-model="confirmPassword" type="password" autocomplete="new-password" required />
        </label>
        <p v-if="passwordError" class="stage-dialog__error">{{ passwordError }}</p>
        <div class="stage-dialog__actions">
          <button type="button" class="stage-dialog__btn" :disabled="passwordBusy" @click="closePasswordDialog">
            取消
          </button>
          <button type="submit" class="stage-dialog__btn stage-dialog__btn--primary" :disabled="passwordBusy">
            {{ passwordBusy ? '保存中…' : '保存新密码' }}
          </button>
        </div>
      </form>
    </dialog>

    <dialog ref="deviceDialog" class="stage-dialog" aria-labelledby="stage-device-title">
      <form class="stage-dialog__form" @submit.prevent="submitUnbind">
        <p class="stage-dialog__kicker">学海设备</p>
        <h2 id="stage-device-title" class="stage-dialog__title">解绑主学海设备</h2>
        <p class="stage-dialog__hint">
          解绑后，当前账号回到“未绑定设备”状态。请输入密码确认。
        </p>
        <p class="stage-dialog__device">{{ primaryDeviceId || '未绑定' }}</p>
        <label class="stage-dialog__field">
          <span>当前密码</span>
          <input v-model="unbindPassword" type="password" autocomplete="current-password" required />
        </label>
        <p v-if="deviceError" class="stage-dialog__error">{{ deviceError }}</p>
        <div class="stage-dialog__actions">
          <button type="button" class="stage-dialog__btn" :disabled="deviceBusy" @click="closeDeviceDialog">
            取消
          </button>
          <button type="submit" class="stage-dialog__btn stage-dialog__btn--danger" :disabled="deviceBusy">
            {{ deviceBusy ? '解绑中…' : '确认解绑' }}
          </button>
        </div>
      </form>
    </dialog>
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
  --gyro-y: 0deg;
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
  color: rgb(196 226 255 / 0.86);
  background: transparent;
  border: 1px solid rgb(172 210 255 / 0.22);
  border-radius: 2px;
  cursor: pointer;
  transition: all 150ms ease-out;
  text-align: left;
}

.dock-btn:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.dock-btn:not(:disabled):not(.is-active):hover {
  background: rgb(111 215 196 / 0.10);
  border-color: rgb(111 215 196 / 0.32);
  color: #eef6ff;
}

.dock-btn:not(:disabled):active,
.dock-btn.is-active {
  background: #6fd7c4;
  border-color: #6fd7c4;
  color: #06121f;
}

.dock-btn:focus-visible {
  outline: 2px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
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
  transform: scale(var(--scene-scale, 1)) rotateX(var(--gyro-x, 0deg))
    rotateZ(var(--gyro-z, 0deg)) rotateY(var(--gyro-y, 0deg));
  transition: transform 180ms ease-out;
}

/* A dragged card must stay above the fixed dock/HUD instead of sliding under
   them and disappearing at the page edge. */
.stage.is-card-dragging .stage-scene {
  z-index: 140;
}

@media (min-width: 901px) {
  /* Volunteer board: cards live above the fixed dock/pager so a card can be
     dragged anywhere and still stay visible. The scene itself is transparent
     to pointer events, so the dock/pager/zoom HUD keep working in the gaps. */
  .stage.is-volunteers .stage-scene {
    z-index: 140;
    pointer-events: none;
  }

  .stage.is-volunteers .volunteer-card-wrapper {
    pointer-events: auto;
  }
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
  transform: perspective(1000px)
    translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    rotateY(clamp(-35deg, calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)), 35deg))
    rotateX(var(--pointer-x, 0deg));
  transition: transform 100ms ease-out, box-shadow 160ms ease;
  box-shadow:
    0 18px 48px rgb(0 0 0 / 0.38),
    0 2px 0 rgb(52 96 150 / 0.18);
  touch-action: none;
}

.page-track .card {
  touch-action: pan-y;
}

.card:hover {
  box-shadow:
    0 22px 58px rgb(0 0 0 / 0.44),
    0 0 0 1px rgb(169 211 255 / 0.12);
}

.card-brackets {
  position: absolute;
  inset: 0;
  pointer-events: none;
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
  position: relative;
  background: transparent;
  border: none;
  box-shadow: none;
  transform: perspective(1000px)
    translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    rotateY(clamp(-35deg, calc(var(--card-tilt, 0deg) + var(--pointer-y, 0deg)), 35deg))
    rotateX(var(--pointer-x, 0deg));
  transition: transform 100ms ease-out;
  touch-action: none;
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
  padding: 0 0 10px;
  border-bottom: 1px solid rgb(169 211 255 / 0.2);
}

.bar-stage::before {
  content: '';
  position: absolute;
  inset: 0 8%;
  background-image:
    linear-gradient(rgb(169 211 255 / 0.1) 1px, transparent 1px),
    linear-gradient(90deg, rgb(169 211 255 / 0.08) 1px, transparent 1px);
  background-size: 28px 18px;
  opacity: 0.5;
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

.bar-2d {
  position: relative;
  width: 46%;
  max-width: 120px;
  max-height: 100%;
  min-height: 6px;
  border: 1px solid rgb(169 211 255 / 0.36);
  border-radius: 1px;
  background:
    linear-gradient(180deg, rgb(169 211 255 / 0.58), rgb(52 130 245 / 0.2));
  box-shadow:
    0 0 24px rgb(109 180 255 / 0.14),
    inset 0 0 18px rgb(109 180 255 / 0.12);
  transition: height 420ms var(--ease-out);
}

.bar-2d::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgb(220 240 255 / 0.18), transparent);
}

.bar-2d__cap {
  position: absolute;
  left: -5px;
  right: -5px;
  top: -3px;
  height: 3px;
  background: linear-gradient(90deg, transparent, rgb(196 226 255 / 0.9), transparent);
  box-shadow: 0 0 12px rgb(109 180 255 / 0.55);
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
  color: rgb(196 226 255 / 0.86);
  background: transparent;
  border: 1px solid rgb(172 210 255 / 0.22);
  border-radius: 2px;
  cursor: pointer;
  font-family: var(--font-mono);
  letter-spacing: 0.06em;
  transition: all 140ms ease-out;
}

.zoom-btn {
  width: 28px;
  font-size: 14px;
  line-height: 1;
}

.zoom-value {
  min-width: 48px;
  font-size: 10px;
}

.zoom-btn:hover:not(:disabled),
.zoom-value:hover {
  background: rgb(111 215 196 / 0.10);
  border-color: rgb(111 215 196 / 0.32);
  color: #eef6ff;
}

.zoom-btn:active:not(:disabled),
.zoom-value:active {
  background: #6fd7c4;
  color: #06121f;
}

.zoom-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.zoom-btn:focus-visible,
.zoom-value:focus-visible {
  outline: 2px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
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

/* Secondary 3D pages */
.page-track {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 170px 0 60px;
  overflow-y: auto;
  overflow-x: hidden;
}

/* Desktop volunteers: the scattered cards are draggable. The scroll container
   used to clip them at the left/right edge (and behind the dock), so a card
   disappeared after a short drag. The 8-card board fits a desktop viewport;
   keep mobile scrolling untouched. */
@media (min-width: 901px) {
  .page-track--volunteers {
    overflow: visible;
  }
}

.page-heading {
  position: absolute;
  top: 64px;
  left: 50%;
  z-index: 5;
  width: min(560px, calc(100% - 48px));
  transform: translateX(-50%);
  text-align: center;
  pointer-events: none;
}

.page-kicker {
  display: block;
  margin-bottom: 6px;
  color: rgb(111 215 196 / 0.62);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.page-title {
  color: #eef6ff;
  font-size: clamp(24px, 3vw, 38px);
  font-weight: 600;
  letter-spacing: 0.08em;
}

.page-summary {
  margin-top: 6px;
  color: rgb(169 211 255 / 0.55);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
}

.page-state {
  color: rgb(169 211 255 / 0.68);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.06em;
}

.page-state--error {
  color: #ffa1ad;
}

.cluster--volunteer {
  position: relative;
  display: block;
  width: 100%;
  max-width: 1040px;
  height: 500px;
  padding: 0;
  overflow: visible;
}

.volunteer-card-wrapper {
  position: absolute;
  transform-style: preserve-3d;
  transform: translate3d(var(--drag-x, 0px), var(--drag-y, 0px), 0)
    rotate(var(--card-rotate, 0deg)) scale(var(--card-scale, 1));
  transition: transform 100ms ease-out;
  background: rgb(0 0 0 / 0.001);
}

.volunteer-card-wrapper .card {
  width: 100%;
  height: 100%;
  min-height: 0;
  pointer-events: auto;
}

.card--volunteer {
  padding: 20px;
}

.card--volunteer .activity-title {
  font-size: 14px;
  padding-right: 14px;
}

.card--volunteer .activity-meta {
  font-size: 10px;
}

.activity-type {
  margin-top: 7px;
  color: rgb(169 211 255 / 0.48);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.03em;
}

.volunteer-pager {
  position: fixed;
  left: 50%;
  bottom: 34px;
  z-index: 120;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  transform: translateX(-50%);
  background: rgb(8 15 28 / 0.92);
  border: 1px solid rgb(111 215 196 / 0.24);
  box-shadow: 0 0 24px rgb(0 0 0 / 0.4);
}

.pager-btn {
  min-width: 30px;
  height: 28px;
  padding: 0 9px;
  border: var(--astra-btn-border);
  border-radius: var(--astra-btn-radius);
  background: var(--astra-btn-bg);
  color: var(--astra-btn-color);
  font-family: var(--astra-btn-font);
  font-size: var(--astra-btn-size);
  letter-spacing: var(--astra-btn-spacing);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.pager-btn:hover:not(:disabled):not(.is-active) {
  background: var(--astra-btn-hover-bg);
  border-color: var(--astra-btn-hover-border-color);
  color: var(--astra-btn-hover-color);
}

.pager-btn.is-active {
  background: var(--astra-btn-active-bg);
  border-color: var(--astra-btn-active-border);
  color: var(--astra-btn-active-color);
}

.pager-btn:disabled {
  cursor: not-allowed;
  opacity: var(--astra-btn-disabled-opacity);
}

.pager-btn:focus-visible {
  outline: var(--astra-btn-focus-outline);
  outline-offset: 1px;
}

.pager-count {
  margin-left: 4px;
  color: rgb(169 211 255 / 0.42);
  font-family: var(--font-mono);
  font-size: 9px;
  white-space: nowrap;
}

.profile-module {
  width: 100%;
}

.profile-group {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 26px;
  width: 100%;
}

.profile-group__heading {
  text-align: center;
}

.cluster--profile {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: stretch;
  width: 100%;
  max-width: 900px;
  padding: 0;
  gap: 20px;
}

.card--profile {
  display: flex;
  flex-direction: column;
  min-height: 148px;
  padding: 22px;
  text-align: left;
}

.profile-card__label {
  color: rgb(111 215 196 / 0.58);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.profile-card__value {
  margin-top: 9px;
  color: #eef6ff;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.profile-card__meta {
  margin-top: 7px;
  color: rgb(169 211 255 / 0.55);
  font-size: 11px;
  line-height: 1.55;
}

.profile-card__action {
  align-self: flex-start;
  min-height: 32px;
  margin-top: auto;
  padding: 6px 12px;
  border: 1px solid rgb(111 215 196 / 0.32);
  border-radius: 2px;
  background: rgb(14 22 38 / 0.9);
  color: #c4e2ff;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: border-color 160ms ease, background 160ms ease;
}

.profile-card__action:hover:not(:disabled) {
  border-color: rgb(111 215 196 / 0.62);
  background: rgb(20 42 48 / 0.95);
}

.profile-card__action:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.profile-card__action--danger {
  border-color: rgb(255 161 173 / 0.3);
  color: #ffb7c0;
}

.profile-card__action--danger:hover:not(:disabled) {
  border-color: rgb(255 161 173 / 0.65);
  background: rgb(48 20 28 / 0.95);
}

.profile-card__error {
  margin-top: 6px;
  color: #ffa1ad;
  font-size: 10px;
}

.gyro-hud {
  position: fixed;
  right: 24px;
  bottom: 92px;
  z-index: 120;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
}

.gyro-toggle,
.gyro-chip,
.gyro-reset {
  min-height: 26px;
  padding: 4px 9px;
  border: 1px solid rgb(172 210 255 / 0.22);
  border-radius: 2px;
  background: rgb(8 15 28 / 0.92);
  color: rgb(196 226 255 / 0.86);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
}

.gyro-toggle,
.gyro-reset {
  cursor: pointer;
  transition: background 140ms ease-out, border-color 140ms ease-out, color 140ms ease-out;
}

.gyro-toggle:hover:not(:disabled),
.gyro-reset:hover {
  background: rgb(111 215 196 / 0.10);
  border-color: rgb(111 215 196 / 0.32);
  color: #eef6ff;
}

.gyro-toggle:active:not(:disabled),
.gyro-reset:active {
  background: #6fd7c4;
  color: #06121f;
}

.gyro-toggle:focus-visible,
.gyro-reset:focus-visible {
  outline: 2px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
}

.gyro-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.gyro-chip {
  color: rgb(111 215 196 / 0.82);
}

.gyro-error {
  max-width: 160px;
  color: #ffa1ad;
  font-size: 9px;
  text-align: right;
}

.stage-toast {
  position: fixed;
  top: 84px;
  right: 24px;
  z-index: 200;
  padding: 8px 12px;
  border: 1px solid rgb(111 215 196 / 0.28);
  background: rgb(8 15 28 / 0.94);
  color: #c4e2ff;
  font-family: var(--font-mono);
  font-size: 10px;
}

.stage-dialog {
  width: min(420px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 0;
  border: 1px solid rgb(111 215 196 / 0.32);
  border-radius: 2px;
  background: #0b1424;
  color: #eef6ff;
  box-shadow: 0 28px 80px rgb(0 0 0 / 0.6);
  overflow: auto;
}

.stage-dialog::backdrop {
  background: rgb(2 5 10 / 0.72);
}

.stage-dialog__form {
  display: grid;
  gap: 12px;
  padding: 24px;
}

.stage-dialog__kicker {
  color: rgb(111 215 196 / 0.62);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.stage-dialog__title {
  font-size: 22px;
  letter-spacing: 0.06em;
}

.stage-dialog__hint,
.stage-dialog__device {
  color: rgb(169 211 255 / 0.62);
  font-size: 12px;
  line-height: 1.6;
}

.stage-dialog__device {
  padding: 8px 10px;
  border: 1px solid rgb(169 211 255 / 0.14);
  font-family: var(--font-mono);
  font-size: 11px;
}

.stage-dialog__field {
  display: grid;
  gap: 5px;
  color: rgb(169 211 255 / 0.7);
  font-size: 11px;
}

.stage-dialog__field input {
  height: 38px;
  padding: 0 10px;
  border: 1px solid rgb(169 211 255 / 0.22);
  border-radius: 2px;
  background: #07101d;
  color: #eef6ff;
  font: inherit;
}

.stage-dialog__field input:focus-visible {
  outline: 1px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
}

.stage-dialog__error {
  color: #ffa1ad;
  font-size: 11px;
  line-height: 1.5;
}

.stage-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.stage-dialog__btn {
  min-height: 32px;
  padding: 7px 12px;
  border: 1px solid rgb(172 210 255 / 0.22);
  border-radius: 2px;
  background: transparent;
  color: rgb(196 226 255 / 0.86);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: background 140ms ease-out, border-color 140ms ease-out, color 140ms ease-out;
}

.stage-dialog__btn:hover:not(:disabled) {
  background: rgb(111 215 196 / 0.10);
  border-color: rgb(111 215 196 / 0.32);
  color: #eef6ff;
}

.stage-dialog__btn:active:not(:disabled) {
  background: #6fd7c4;
  color: #06121f;
}

.stage-dialog__btn:focus-visible {
  outline: 2px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
}

.stage-dialog__btn--primary {
  border-color: rgb(111 215 196 / 0.52);
  background: rgb(10 34 38 / 0.5);
  color: #dffaf4;
}

.stage-dialog__btn--primary:hover:not(:disabled) {
  border-color: rgb(111 215 196 / 0.78);
  background: rgb(14 46 50 / 0.7);
  color: #ffffff;
}

.stage-dialog__btn--primary:active:not(:disabled) {
  background: #6fd7c4;
  color: #06121f;
}

.stage-dialog__btn--danger {
  border-color: rgb(255 161 173 / 0.46);
  background: rgb(42 20 28 / 0.5);
  color: #ffd3d9;
}

.stage-dialog__btn--danger:hover:not(:disabled) {
  border-color: rgb(255 161 173 / 0.68);
  background: rgb(52 24 32 / 0.75);
  color: #ffffff;
}

.stage-dialog__btn--danger:active:not(:disabled) {
  background: rgb(255 100 120 / 0.9);
  color: #ffffff;
}

.stage-dialog__btn:disabled {
  cursor: not-allowed;
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

  .bar-2d {
    width: 60%;
    max-height: 100%;
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

  .page-track {
    align-items: flex-start;
    padding: 150px 0 132px;
    overflow-y: auto;
  }

  .page-heading {
    top: 52px;
    width: calc(100% - 32px);
  }

  .page-title {
    font-size: 24px;
  }

  .cluster--volunteer {
    display: flex;
    flex-direction: column;
    gap: 10px;
    height: auto;
    max-height: none;
    padding: 0 16px 8px;
    overflow: visible;
  }

  .volunteer-card-wrapper {
    position: static;
    width: 100%;
    height: auto;
    transform: none;
    left: 0;
    top: 0;
  }

  .volunteer-card-wrapper .card {
    height: auto;
    min-height: 104px;
  }

  .card--volunteer {
    padding: 16px;
  }

  .volunteer-pager {
    bottom: 18px;
    max-width: calc(100vw - 24px);
    gap: 4px;
    padding: 6px;
  }

  .pager-btn {
    min-width: 26px;
    height: 26px;
    padding: 0 6px;
    font-size: 9px;
  }

  .pager-count {
    display: none;
  }

  .profile-group {
    gap: 16px;
    padding: 0 12px;
  }

  .profile-group__heading .page-title {
    font-size: 24px;
  }

  .cluster--profile {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    max-height: none;
    padding: 0 12px 8px;
    overflow: visible;
  }

  .card--profile {
    min-height: 132px;
    padding: 14px;
  }

  .profile-card__value {
    font-size: 16px;
  }

  .profile-card__meta {
    font-size: 10px;
  }

  .profile-card__action {
    min-height: 28px;
    padding: 5px 8px;
    font-size: 9px;
  }

  .gyro-hud {
  position: fixed;
  right: 24px;
  bottom: 92px;
  z-index: 120;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
}

.gyro-toggle,
.gyro-chip,
.gyro-reset {
  min-height: 26px;
  padding: 4px 9px;
  border: 1px solid rgb(172 210 255 / 0.22);
  border-radius: 2px;
  background: rgb(8 15 28 / 0.92);
  color: rgb(196 226 255 / 0.86);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
}

.gyro-toggle,
.gyro-reset {
  cursor: pointer;
  transition: background 140ms ease-out, border-color 140ms ease-out, color 140ms ease-out;
}

.gyro-toggle:hover:not(:disabled),
.gyro-reset:hover {
  background: rgb(111 215 196 / 0.10);
  border-color: rgb(111 215 196 / 0.32);
  color: #eef6ff;
}

.gyro-toggle:active:not(:disabled),
.gyro-reset:active {
  background: #6fd7c4;
  color: #06121f;
}

.gyro-toggle:focus-visible,
.gyro-reset:focus-visible {
  outline: 2px solid rgb(111 215 196 / 0.7);
  outline-offset: 1px;
}

.gyro-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.gyro-chip {
  color: rgb(111 215 196 / 0.82);
}

.gyro-error {
  max-width: 160px;
  color: #ffa1ad;
  font-size: 9px;
  text-align: right;
}

.stage-toast {
    top: 70px;
    right: 12px;
    max-width: calc(100vw - 24px);
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

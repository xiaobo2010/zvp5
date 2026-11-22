/**
 * Ambient Light Engine
 * ----------------------------------------------------------------------------
 * 背景光团的运动内核。只做三件事：
 * 1. 用不同周期、不同相位的三角波叠加出「看不出循环」的自然漂移；
 * 2. 用弹簧把指针位移接到每个光团上（不同强度 → 延迟视差），指针停下后回中；
 * 3. 每帧只写 transform / opacity，全部走合成层，不触发重排。
 *
 * 颜色由 CSS 决定（见 AmbientBackground.vue），主题切换不需要重建引擎。
 */

export interface AmbientBlobSpec {
  id: string
  /** 直径相对视口长边的比例 */
  size: number
  /** 基准位置，0~1 相对视口 */
  x: number
  y: number
  /** 自然漂移振幅，相对视口 */
  driftX: number
  driftY: number
  /** 两条轴的主周期（秒），互质避免同步 */
  periodX: number
  periodY: number
  /** 相位偏移，错开不同光团 */
  phase: number
  /** 指针视差强度 0~1.8 */
  parallax: number
  /** 呼吸的幅度与周期 */
  breathe: number
  breathePeriod: number
  baseOpacity: number
}

/** 六团光：香槟金 / 珍珠白 / 琥珀 / 淡蓝 / 灰紫 / 暖象牙 */
export const AMBIENT_BLOBS: AmbientBlobSpec[] = [
  {
    id: 'gold',
    size: 0.86,
    x: 0.16,
    y: 0.12,
    driftX: 0.075,
    driftY: 0.055,
    periodX: 73,
    periodY: 97,
    phase: 0.4,
    parallax: 1.45,
    breathe: 0.05,
    breathePeriod: 31,
    baseOpacity: 1
  },
  {
    id: 'pearl',
    size: 0.72,
    x: 0.82,
    y: 0.2,
    driftX: 0.06,
    driftY: 0.07,
    periodX: 89,
    periodY: 67,
    phase: 2.1,
    parallax: 0.55,
    breathe: 0.04,
    breathePeriod: 27,
    baseOpacity: 1
  },
  {
    id: 'amber',
    size: 0.64,
    x: 0.62,
    y: 0.78,
    driftX: 0.08,
    driftY: 0.06,
    periodX: 61,
    periodY: 83,
    phase: 4.2,
    parallax: 0.95,
    breathe: 0.06,
    breathePeriod: 37,
    baseOpacity: 1
  },
  {
    id: 'blue',
    size: 0.58,
    x: 0.06,
    y: 0.72,
    driftX: 0.055,
    driftY: 0.065,
    periodX: 79,
    periodY: 53,
    phase: 1.2,
    parallax: 0.35,
    breathe: 0.05,
    breathePeriod: 29,
    baseOpacity: 1
  },
  {
    id: 'lavender',
    size: 0.5,
    x: 0.9,
    y: 0.62,
    driftX: 0.05,
    driftY: 0.05,
    periodX: 101,
    periodY: 71,
    phase: 3.4,
    parallax: 0.7,
    breathe: 0.05,
    breathePeriod: 41,
    baseOpacity: 1
  },
  {
    id: 'ivory',
    size: 0.94,
    x: 0.44,
    y: 0.44,
    driftX: 0.045,
    driftY: 0.04,
    periodX: 113,
    periodY: 59,
    phase: 5.3,
    parallax: 0.18,
    breathe: 0.03,
    breathePeriod: 47,
    baseOpacity: 1
  }
]

/** 按屏幕规模裁剪光团数量：小屏只保留 4 团，减少合成压力 */
export function ambientSpecs(compact: boolean): AmbientBlobSpec[] {
  return compact ? AMBIENT_BLOBS.slice(0, 4) : AMBIENT_BLOBS
}

interface BlobState {
  spec: AmbientBlobSpec
  el: HTMLElement
  /** 弹簧位移（px） */
  ox: number
  oy: number
  vx: number
  vy: number
  size: number
}

export interface AmbientEngineOptions {
  /** 系统开启减少动态效果时只渲染一帧静态画面 */
  reducedMotion?: boolean
  /** 触摸设备关闭指针视差 */
  pointerEnabled?: boolean
  /** 小屏降低数量与幅度 */
  compact?: boolean
}

const TWO_PI = Math.PI * 2

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

export class AmbientLightEngine {
  private blobs: BlobState[] = []
  private options: Required<AmbientEngineOptions>

  private width = 1
  private height = 1
  private time = 0
  private startedAt = 0
  private frame = 0
  private running = false

  /** 指针目标（-1~1）与平滑值 */
  private targetX = 0
  private targetY = 0
  private pointerX = 0
  private pointerY = 0
  /** 指针速度 0~1，用于放大视差幅度 */
  private energy = 0

  private onResize = () => {
    this.measure()
    if (this.options.reducedMotion) this.renderStatic()
  }

  private onVisibility = () => {
    if (this.options.reducedMotion) return
    if (document.hidden) this.stop()
    else this.startLoop()
  }

  constructor(_host: HTMLElement, elements: HTMLElement[], options: AmbientEngineOptions = {}) {
    this.options = {
      reducedMotion: options.reducedMotion ?? false,
      pointerEnabled: options.pointerEnabled ?? true,
      compact: options.compact ?? false
    }

    const specs = ambientSpecs(this.options.compact)
    this.blobs = specs.map((spec, index) => ({
      spec,
      el: elements[index],
      ox: 0,
      oy: 0,
      vx: 0,
      vy: 0,
      size: 1
    })).filter((blob): blob is BlobState => Boolean(blob.el))

    this.measure()
    this.startedAt = performance.now()
    this.time = 0
  }

  start() {
    if (this.options.reducedMotion) {
      this.renderStatic()
      return
    }
    this.startLoop()
    window.addEventListener('resize', this.onResize, { passive: true })
    document.addEventListener('visibilitychange', this.onVisibility)
  }

  destroy() {
    this.stop()
    window.removeEventListener('resize', this.onResize)
    document.removeEventListener('visibilitychange', this.onVisibility)
    this.blobs = []
  }

  /** 指针归一化坐标（-1~1）+ 瞬时速度（px/ms），由外部 rAF 合并后调用 */
  pushPointer(x: number, y: number, speed: number) {
    this.targetX = clamp(x, -1, 1)
    this.targetY = clamp(y, -1, 1)
    // 速度换算成 0~1 的能量：快移动时视差幅度最多 +70%
    this.energy = Math.max(this.energy, clamp(speed / 2.4, 0, 1))
  }

  releasePointer() {
    this.targetX = 0
    this.targetY = 0
  }

  private measure() {
    this.width = Math.max(1, window.innerWidth)
    this.height = Math.max(1, window.innerHeight)
    const base = Math.max(this.width, this.height)
    for (const blob of this.blobs) {
      blob.size = Math.round(base * blob.spec.size * (this.options.compact ? 0.86 : 1))
      blob.el.style.width = `${blob.size}px`
      blob.el.style.height = `${blob.size}px`
    }
  }

  private startLoop() {
    if (this.running) return
    this.running = true
    const tick = (now: number) => {
      if (!this.running) return
      this.step(now)
      this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  private stop() {
    this.running = false
    if (this.frame) cancelAnimationFrame(this.frame)
    this.frame = 0
  }

  private renderStatic() {
    this.time = 0
    this.pointerX = 0
    this.pointerY = 0
    this.blobs.forEach((blob) => this.paint(blob, 0))
  }

  private step(now: number) {
    this.time = (now - this.startedAt) / 1000

    // 指针平滑：指数插值 → 无论帧率如何都不会抖动
    const follow = 1 - Math.pow(0.0018, 1 / 60)
    this.pointerX += (this.targetX - this.pointerX) * follow
    this.pointerY += (this.targetY - this.pointerY) * follow
    this.energy *= 0.96

    for (const blob of this.blobs) this.paint(blob, this.time)
  }

  private paint(blob: BlobState, t: number) {
    const { spec } = blob
    const amplitude = this.options.pointerEnabled ? 34 + this.energy * 26 : 0

    // 弹簧：目标位移由指针 × 视差强度决定，停下后自然回中
    const targetOx = this.options.pointerEnabled ? this.pointerX * amplitude * spec.parallax : 0
    const targetOy = this.options.pointerEnabled ? this.pointerY * amplitude * spec.parallax : 0
    const stiffness = 42
    const damping = 13
    blob.vx += (targetOx - blob.ox) * stiffness * (1 / 60) - blob.vx * damping * (1 / 60)
    blob.vy += (targetOy - blob.oy) * stiffness * (1 / 60) - blob.vy * damping * (1 / 60)
    blob.ox += blob.vx * (1 / 60)
    blob.oy += blob.vy * (1 / 60)

    // 双频叠加：主周期之外再叠一个 0.47 倍的慢波，轨迹不会回到同一点
    const ax = t * (TWO_PI / spec.periodX) + spec.phase
    const ay = t * (TWO_PI / spec.periodY) + spec.phase * 1.37
    const driftX =
      (Math.sin(ax) * 0.62 + Math.sin(ax * 0.47 + 1.1) * 0.38) * spec.driftX * this.width
    const driftY =
      (Math.cos(ay) * 0.62 + Math.cos(ay * 0.53 + 0.6) * 0.38) * spec.driftY * this.height

    const breathe = 1 + Math.sin(t * (TWO_PI / spec.breathePeriod) + spec.phase) * spec.breathe
    const centerX = spec.x * this.width + driftX + blob.ox
    const centerY = spec.y * this.height + driftY + blob.oy
    const x = centerX - blob.size / 2
    const y = centerY - blob.size / 2

    blob.el.style.transform = `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0) scale(${breathe.toFixed(4)})`
    blob.el.style.opacity = (
      spec.baseOpacity *
      (0.78 + 0.22 * Math.sin(t * (TWO_PI / spec.breathePeriod) + spec.phase + 1.9)) *
      (this.options.compact ? 0.72 : 1)
    ).toFixed(3)
  }
}

import { reportSignals } from '@/api/platformGuard'
import type { PlatformSignal, PlatformSignalName } from '@/api/platformGuard'

const KEY_PREFIX = 'zvms.platformGuard.'

/** 检测结果缓存:同一分钟内多次调用(路由切换、手动触发)不重复采样。 */
const RESULT_KEY = `${KEY_PREFIX}result`
const RESULT_TTL_MS = 60_000

/** 巡检周期。与后端去重窗口对齐的「采样」节奏,也是 debugger 计时的最小间隔。 */
const TICK_MS = 60_000

/** devtools 需要连续命中这么多次才上报,避免窗口尺寸抖动、偶发卡顿误判。 */
const DEVTOOLS_STREAK = 3
const DEVTOOLS_GAP_PX = 160
const DEBUGGER_SUSPEND_MS = 120

/** 每信号上报节流:普通信号 5 分钟、devtools 30 分钟,与后端 ban_violations 去重窗口一致。 */
const DEFAULT_THROTTLE_MS = 5 * 60_000
const THROTTLE_MS: Record<PlatformSignalName, number> = {
  iframe: DEFAULT_THROTTLE_MS,
  bridge: DEFAULT_THROTTLE_MS,
  devtools: 30 * 60_000
}

interface CachedResult {
  at: number
  signals: PlatformSignal[]
}

let timer: ReturnType<typeof setInterval> | undefined
let lastSampleAt = 0
let devtoolsStreak = 0

// localStorage 在隐私模式/配额满时会抛异常;巡检是旁路能力,不能因此影响主流程。
function readKey(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeKey(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 存不下就退化成「每次都检测」,不做额外补偿 */
  }
}

function readCache(now: number): PlatformSignal[] | null {
  const raw = readKey(RESULT_KEY)
  if (!raw) {
    return null
  }
  try {
    const parsed = JSON.parse(raw) as CachedResult
    if (!parsed || typeof parsed.at !== 'number' || !Array.isArray(parsed.signals)) {
      return null
    }
    if (now - parsed.at >= RESULT_TTL_MS) {
      return null
    }
    return parsed.signals.filter((item) => item && typeof item.signal === 'string')
  } catch {
    return null
  }
}

function writeCache(now: number, signals: PlatformSignal[]): void {
  writeKey(RESULT_KEY, JSON.stringify({ at: now, signals }))
}

/**
 * 学海客户端判定。只有「明确是学海浏览器」时才校验桥：
 * 桥对象存在，或 UA 带 `xhbrowser`。资料中心 App 的 UA 只有 `userId/<id>`
 * 且本来就不注入桥，若按它判违规，所有资料中心用户都会被误伤。
 */
function looksLikeXuehaiClient(): boolean {
  return typeof window.xhBrowserJava !== 'undefined' || /xhbrowser/i.test(navigator.userAgent)
}

/** 桥存在且 getUserId 可调用并返回合法 id 才算「正常」;抛异常/返回空都算缺失。 */
function bridgeUsable(): boolean {
  const bridge = window.xhBrowserJava
  if (!bridge || typeof bridge.getUserId !== 'function') {
    return false
  }
  try {
    const id = Number(bridge.getUserId())
    return Number.isFinite(id) && id > 0
  } catch {
    return false
  }
}

/**
 * 单次 devtools/tamper 采样,返回命中原因(未命中返回 null)。
 * tamper 并入 devtools:页面根节点或平台标记被摘掉,与调试器注入同属「页面被外力改动」,
 * 合并成一个 signal 可让后端只依赖 platform_misuse 一条规则。
 */
function probeDevtools(): string | null {
  if (!document.getElementById('app')) {
    return 'tamper:app-missing'
  }
  if (!document.querySelector('meta[name="zvms-platform"]')) {
    return 'tamper:meta-missing'
  }
  const widthGap = window.outerWidth - window.innerWidth
  if (widthGap > DEVTOOLS_GAP_PX) {
    return `devtools:width+${widthGap}`
  }
  const heightGap = window.outerHeight - window.innerHeight
  if (heightGap > DEVTOOLS_GAP_PX) {
    return `devtools:height+${heightGap}`
  }
  // 尺寸特征不成立时补一次 debugger 计时:没有调试器时该语句开销接近 0,
  // 有断点/单步时会阻塞在这里,performance.now() 的差值即为被挂起的毫秒数。
  const started = performance.now()
  debugger
  const suspended = performance.now() - started
  return suspended > DEBUGGER_SUSPEND_MS ? `devtools:suspended+${Math.round(suspended)}` : null
}

/**
 * 采集当前命中的所有信号。devtools 带采样间隔与连续命中计数器,
 * 因此本函数被高频调用也不会放大 debugger 开销。
 */
export function detectSignals(now: number = Date.now()): PlatformSignal[] {
  const signals: PlatformSignal[] = []

  if (window.top !== window.self) {
    signals.push({ signal: 'iframe', detail: 'top!=self' })
  }

  if (looksLikeXuehaiClient() && !bridgeUsable()) {
    signals.push({ signal: 'bridge', detail: 'xhBrowserJava.getUserId unavailable' })
  }

  if (now - lastSampleAt >= TICK_MS) {
    lastSampleAt = now
    const hit = probeDevtools()
    if (hit) {
      devtoolsStreak += 1
      if (devtoolsStreak >= DEVTOOLS_STREAK) {
        signals.push({ signal: 'devtools', detail: hit })
        // 归零:上报后重新攒连续命中,避免一次命中在后续每个窗口里反复上报
        devtoolsStreak = 0
      }
    } else {
      devtoolsStreak = 0
    }
  }

  return signals
}

function lastReportedAt(signal: PlatformSignalName): number {
  const value = Number(readKey(`${KEY_PREFIX}report.${signal}`))
  return Number.isFinite(value) ? value : 0
}

function throttleFor(signal: PlatformSignalName): number {
  return THROTTLE_MS[signal] ?? DEFAULT_THROTTLE_MS
}

function isThrottled(signal: PlatformSignalName, now: number): boolean {
  return now - lastReportedAt(signal) < throttleFor(signal)
}

/**
 * 一次完整巡检:未登录或页面不可见时直接跳过(后台标签页不计违规),
 * 否则读 60s 内的检测缓存,过滤掉节流中的信号后上报。
 */
export async function checkPlatform(): Promise<void> {
  if (!readKey('token')) {
    return
  }
  if (document.visibilityState !== 'visible') {
    return
  }

  const now = Date.now()
  const cached = readCache(now)
  const signals = cached ?? detectSignals(now)
  if (!cached) {
    writeCache(now, signals)
  }
  if (signals.length === 0) {
    return
  }

  const pending = signals.filter((item) => !isThrottled(item.signal, now))
  if (pending.length === 0) {
    return
  }

  const delivered = await reportSignals(pending)
  if (!delivered) {
    // 失败不记节流:下一分钟恢复网络后还能补报这一次
    return
  }
  for (const item of pending) {
    writeKey(`${KEY_PREFIX}report.${item.signal}`, String(now))
  }
}

/** 幂等启动:登录后调用,重复调用不会叠加定时器。 */
export function startPlatformGuard(): void {
  if (timer) {
    return
  }
  void checkPlatform()
  timer = setInterval(() => {
    void checkPlatform()
  }, TICK_MS)
}

/** 登出/卸载时停止;已攒的 devtools 连续计数保留,重新登录后继续累计。 */
export function stopPlatformGuard(): void {
  if (!timer) {
    return
  }
  clearInterval(timer)
  timer = undefined
}

import { reportSignals } from '@/api/platformGuard'
import type { PlatformSignal, PlatformSignalName } from '@/api/platformGuard'

const KEY_PREFIX = 'zvms.platformGuard.'

/** 巡检周期。与后端去重窗口对齐的「采样」节奏,也是 debugger 计时的最小间隔。 */
const TICK_MS = 60_000

/**
 * 按键隔离:所有 localStorage 键都带当前账号前缀,同机切换账号时
 * 旧账号的节流/检测状态不会作用于新账号。
 */
let account = 'anon'
function keyName(key: string): string {
  return `${KEY_PREFIX}${account}.${key}`
}

function resetAccountKeys(): void {
  try {
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const name = localStorage.key(i)
      if (name?.startsWith(`${KEY_PREFIX}${account}.`)) {
        localStorage.removeItem(name)
      }
    }
  } catch {
    /* 隐私模式/配额满时无法访问,静默放弃 */
  }
}

/** 登录后由调用方注入账号,切换账号时旧账号的会话状态一并作废。 */
export function setPlatformGuardAccount(oid: string): void {
  if (oid === account) {
    return
  }
  account = oid || 'anon'
  iframeReported = false
  bridgeReported = false
  tamperAppReported = false
  tamperMetaReported = false
  devtoolsStrongStreak = 0
  devtoolsWeakStreak = 0
}

/** 登出时清理当前账号的全部巡检键。 */
export function clearPlatformGuardData(): void {
  resetAccountKeys()
  iframeReported = false
  bridgeReported = false
  tamperAppReported = false
  tamperMetaReported = false
  devtoolsStrongStreak = 0
  devtoolsWeakStreak = 0
}

// localStorage 在隐私模式/配额满时会抛异常;巡检是旁路能力,不能因此影响主流程。
function readKey(key: string): string | null {
  try {
    return localStorage.getItem(keyName(key))
  } catch {
    return null
  }
}

function writeKey(key: string, value: string): void {
  try {
    localStorage.setItem(keyName(key), value)
  } catch {
    /* 存不下就退化成「每次都检测」,不做额外补偿 */
  }
}

/**
 * 跨域嵌套判定。正常嵌入(同源、或 referrer 为空/本站)不上报;
 * 只在 referrer 主机与当前站点不同时才视为跨域嵌套候选。
 */
function iframeCandidate(): { path: string; refHost: string } | null {
  if (window.top === window.self) {
    return null
  }
  const referrer = document.referrer
  if (!referrer) {
    return null
  }
  let refHost: string
  try {
    refHost = new URL(referrer).hostname
  } catch {
    return null
  }
  if (refHost === location.hostname) {
    return null
  }
  return { path: location.pathname, refHost }
}

/**
 * iframe 上报条件:跨域嵌套且持续存在超过 120 秒,且每次会话最多一次。
 * 首次满足「top!=self + 异源 referrer」时记录时间戳,之后每个巡检周期
 * 复查是否仍处于嵌套状态。
 */
const IFRAME_MIN_DURATION_MS = 120_000

/** iframe / bridge 每个会话只报一次(跨会话回到 0 由新一轮检测重新累积)。 */
let iframeReported = false

function detectIframe(): PlatformSignal | null {
  if (iframeReported) {
    return null
  }
  const candidate = iframeCandidate()
  if (!candidate) {
    // 嵌套解除:计时作废,下次重新嵌套时重新起算
    writeKey('iframe.firstAt', '')
    return null
  }
  let firstAt = Number(readKey('iframe.firstAt'))
  if (!Number.isFinite(firstAt) || firstAt <= 0) {
    firstAt = Date.now()
    writeKey('iframe.firstAt', String(firstAt))
    return null
  }
  if (Date.now() - firstAt < IFRAME_MIN_DURATION_MS) {
    return null
  }
  iframeReported = true
  writeKey('iframe.firstAt', '')
  return { signal: 'iframe', detail: `path=${candidate.path} ref=${candidate.refHost}` }
}

/**
 * 学海客户端候选判定。只有 UA 明确带 `xhbrowser` 标记时才校验桥。
 * 资料中心 App 的 UA 只有 `userId/<id>`,普通 PC 浏览器更没有,
 * 都不算候选,永不报 bridge。
 */
function looksLikeXuehaiClient(): boolean {
  return /xhbrowser/i.test(navigator.userAgent)
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

let bridgeReported = false

/**
 * bridge 上报条件:候选客户端首次检测到桥缺失后,连续 3 次探测(间隔 ≥10 秒,
 * 实际由 60 秒巡检周期保证)都失败,且距首次缺失超过 30 秒才上报,
 * 用以覆盖客户端升级、桥延迟注入、WebView 首屏竞态。任一次探测恢复正常即清零。
 */
const BRIDGE_MIN_WAIT_MS = 30_000
const BRIDGE_CONSECUTIVE_FAILURES = 3

function detectBridge(): PlatformSignal | null {
  if (bridgeReported) {
    return null
  }
  if (!looksLikeXuehaiClient()) {
    if (Number(readKey('bridge.failCount')) > 0) {
      writeKey('bridge.failCount', '0')
      writeKey('bridge.firstAt', '')
    }
    return null
  }
  if (bridgeUsable()) {
    writeKey('bridge.failCount', '0')
    writeKey('bridge.firstAt', '')
    return null
  }
  const firstAt = Date.now()
  let failCount = Number(readKey('bridge.failCount'))
  if (!Number.isFinite(failCount) || failCount <= 0) {
    writeKey('bridge.firstAt', String(firstAt))
    failCount = 1
  } else {
    failCount += 1
  }
  writeKey('bridge.failCount', String(failCount))
  const origin = Number(readKey('bridge.firstAt')) || firstAt
  if (failCount < BRIDGE_CONSECUTIVE_FAILURES) {
    return null
  }
  if (Date.now() - origin < BRIDGE_MIN_WAIT_MS) {
    return null
  }
  bridgeReported = true
  writeKey('bridge.failCount', '0')
  writeKey('bridge.firstAt', '')
  const version = /xhbrowser\/([\w.-]+)/i.exec(navigator.userAgent)?.[1]
  const uaDigest = navigator.userAgent.slice(0, 64)
  const detail = version
    ? `version=${version} ua=${uaDigest}`
    : `ua=${uaDigest}`
  return { signal: 'bridge', detail }
}

/**
 * devtools 命中分两级,不同证据用不同连击阈值:
 * - strong(强证据):debugger 真实挂起 > 300ms。开着开发者工具时注入的
 *   debugger 会真的挂起主线程,低端设备上连续 3 个采样周期都出现几乎
 *   不可能是巧合 —— 未停靠的独立工具窗口(不压缩窗口)只能靠这条兜住。
 * - weak(弱证据):窗口宽高双向压缩都 > 240px(停靠式面板的典型形态),
 *   连续 5 个采样周期才成立,避免误伤分屏/缩放。
 *
 * 同一采样周期里 strong 优先,只推进一个计数,不会双计数导致重复上报;
 * 两个条件都不成立时,两个连击计数一并清零。
 */
const DEVTOOLS_GAP_PX = 240
const DEBUGGER_SUSPEND_MS = 300
const DEVTOOLS_STRONG_STREAK = 3
const DEVTOOLS_WEAK_STREAK = 5

type DevtoolsHit = 'strong' | 'weak' | 'none'

let devtoolsStrongStreak = 0
let devtoolsWeakStreak = 0

/**
 * 单次 devtools 采样,归一成 strong / weak / none 三档:
 * debugger 挂起 >300ms 才算 strong(120-300ms 区间的长任务/GC/低端平板
 * 卡顿不算),窗口宽高双向压缩 >240px 才算 weak,两者都不成立就是 none。
 * debugger 每次都要测:停靠式面板同时满足 weak 与 strong 时,生效的是
 * strong(阈值更低、证据更硬)。
 */
function probeDevtoolsConditions(): DevtoolsHit {
  const widthGap = window.outerWidth - window.innerWidth
  const heightGap = window.outerHeight - window.innerHeight
  const squeezed = widthGap > DEVTOOLS_GAP_PX && heightGap > DEVTOOLS_GAP_PX

  const started = performance.now()
  debugger
  const suspended = performance.now() - started
  if (suspended > DEBUGGER_SUSPEND_MS) {
    return 'strong'
  }
  if (squeezed) {
    return 'weak'
  }
  return 'none'
}

/**
 * tamper 检查(强度收敛版):仅在 document.readyState === 'complete'
 * 且页面已加载 10 秒后检查一次;#app 存在但 meta 缺失、或反之,各自限一次/会话。
 * 后端信号白名单没有独立的 tamper,因此沿用 devtools 信号名上报。
 */
const TAMPER_MIN_SESSION_MS = 10_000

let tamperAppReported = false
let tamperMetaReported = false

/** 页面加载起点:每次页面加载(刷新)重新起算,不持久化——刷新是正常的加载行为。 */
let sessionStartAtMs = 0

function sessionStartAt(now: number): number {
  if (!sessionStartAtMs) {
    sessionStartAtMs = now
  }
  return sessionStartAtMs
}

function detectTamper(now: number): PlatformSignal | null {
  if (document.readyState !== 'complete') {
    return null
  }
  if (now - sessionStartAt(now) < TAMPER_MIN_SESSION_MS) {
    return null
  }
  const hasApp = Boolean(document.getElementById('app'))
  const hasMeta = Boolean(document.querySelector('meta[name="zvms-platform"]'))
  if (hasApp && !hasMeta && !tamperMetaReported) {
    tamperMetaReported = true
    return { signal: 'devtools', detail: 'tamper:meta-missing' }
  }
  if (hasMeta && !hasApp && !tamperAppReported) {
    tamperAppReported = true
    return { signal: 'devtools', detail: 'tamper:app-missing' }
  }
  return null
}

/**
 * 采集当前命中的信号。iframe / bridge / tamper 自带会话级状态机,
 * devtools 分 strong / weak 两级连击:debugger 挂起连续 3 个采样周期即报,
 * 窗口双向压缩连续 5 个采样周期才报,高频调用也不会放大开销。
 */
export function detectSignals(now: number = Date.now()): PlatformSignal[] {
  const signals: PlatformSignal[] = []
  const iframe = detectIframe()
  if (iframe) {
    signals.push(iframe)
  }
  const bridge = detectBridge()
  if (bridge) {
    signals.push(bridge)
  }
  const tamper = detectTamper(now)
  if (tamper) {
    signals.push(tamper)
  }
  const hit = probeDevtoolsConditions()
  if (hit === 'strong') {
    // strong 优先:weak 计数不推进,同一周期不会双计数
    devtoolsWeakStreak = 0
    devtoolsStrongStreak += 1
    if (devtoolsStrongStreak >= DEVTOOLS_STRONG_STREAK) {
      devtoolsStrongStreak = 0
      signals.push({ signal: 'devtools', detail: 'panel-detected' })
    }
  } else if (hit === 'weak') {
    devtoolsStrongStreak = 0
    devtoolsWeakStreak += 1
    if (devtoolsWeakStreak >= DEVTOOLS_WEAK_STREAK) {
      devtoolsWeakStreak = 0
      signals.push({ signal: 'devtools', detail: 'panel-detected' })
    }
  } else {
    devtoolsStrongStreak = 0
    devtoolsWeakStreak = 0
  }
  return signals
}

/** 同一信号 10 分钟内不重复;一次上报最多携带 2 个信号。 */
const THROTTLE_MS = 10 * 60_000
const MAX_BATCH = 2

function lastReportedAt(signal: PlatformSignalName): number {
  const value = Number(readKey(`report.${signal}`))
  return Number.isFinite(value) ? value : 0
}

function isThrottled(signal: PlatformSignalName, now: number): boolean {
  return now - lastReportedAt(signal) < THROTTLE_MS
}

/**
 * 一次完整巡检:未登录或页面不可见时直接跳过(后台标签页不计违规),
 * 否则采集信号、按节流过滤后取前 2 个上报。不为了「凑数」重复发送。
 */
export async function checkPlatform(): Promise<void> {
  if (!readKeyRaw('token')) {
    return
  }
  // 页面不可见(切到别的标签页/最小化)时不采样:连击计数也不推进,
  // 否则后台挂机时长会被算成「持续命中」。
  if (document.hidden || document.visibilityState !== 'visible') {
    return
  }

  const now = Date.now()
  const signals = detectSignals(now)
  if (signals.length === 0) {
    return
  }

  const pending = signals.filter((item) => !isThrottled(item.signal, now)).slice(0, MAX_BATCH)
  if (pending.length === 0) {
    return
  }

  const delivered = await reportSignals(pending)
  if (!delivered) {
    // 失败不记节流:下一分钟恢复网络后还能补报这一次
    return
  }
  for (const item of pending) {
    writeKey(`report.${item.signal}`, String(now))
  }
}

function readKeyRaw(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
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

/** 登出/卸载时停止。 */
export function stopPlatformGuard(): void {
  if (!timer) {
    return
  }
  clearInterval(timer)
  timer = undefined
}

let timer: ReturnType<typeof setInterval> | undefined

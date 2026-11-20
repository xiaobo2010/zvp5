/**
 * 低 GPU 设备探测 + 页面可见性标志。
 *
 * 目标设备：三星 Galaxy Tab S6 Lite（SM-P615C / SM-P620 等，Mali 级 GPU、
 * 4 GB RAM、60Hz）。同一套 phi 主题在桌面/新款平板上是设计本体，不能降级；
 * 在这类设备上，全屏蜂窝网格、扫光、流光、判定线脉冲会持续占用合成带宽，
 * 滚动与首屏进场因此掉帧。
 *
 * 做法：只在启动时（mount 之前）判定一次，给 `<html>` 打 `data-low-gpu`，
 * 由 `assets/styles/perf-tablet.css` 的静态规则降载（不参与运行时重排）。
 * 判定规则刻意保守——宁可漏判（退回普通档）也不要误伤新款设备：
 *   * UA 命中已知低端三星型号（这些型号的 UA 里一定带 SM-Pxxx）；
 *   * 或 UA 是 Android 且 deviceMemory <= 4 GB / hardwareConcurrency <= 4
 *     （桌面浏览器即使 4 核也不降级，因为 UA 里没有 Android）；
 *   * `?perf=low` / `?perf=high` 手动覆盖，优先级最高（便于现场排查与回归）。
 *
 * 另附 `installPageVisibilityFlag`：标签页不可见时冻结全部 CSS 动画。
 * 后台标签页本来就不出帧，但 Android WebView/Chrome 在切回前台时会先跑
 * 累积的动画状态；提前暂停可省掉回前台那几帧的合成尖峰。
 */

const LOW_GPU_ATTR = 'data-low-gpu'
const PAGE_HIDDEN_ATTR = 'data-page-hidden'

/** 已知 Mali 级 GPU + 4 GB 内存的三星平板型号（Galaxy Tab S6 Lite 各代）。 */
const LOW_PERF_SAMSUNG_MODELS = ['SM-P620', 'SM-P615C', 'SM-P615', 'SM-P200', 'SM-P355C']

/** Android 设备低于等于该内存（GB）/ 核心数时视为低端。 */
const LOW_MEMORY_GB = 4
const LOW_CORES = 4

export interface PerfEnv {
  userAgent?: string
  deviceMemory?: number
  hardwareConcurrency?: number
  search?: string
}

interface NavigatorWithMemory extends Navigator {
  deviceMemory?: number
}

function readGlobalEnv(): PerfEnv {
  if (typeof navigator === 'undefined') {
    return {}
  }
  const nav = navigator as NavigatorWithMemory
  return {
    userAgent: nav.userAgent,
    deviceMemory: typeof nav.deviceMemory === 'number' ? nav.deviceMemory : undefined,
    hardwareConcurrency:
      typeof nav.hardwareConcurrency === 'number' ? nav.hardwareConcurrency : undefined,
    search: typeof window !== 'undefined' ? window.location.search : ''
  }
}

function matchesSamsungModel(userAgent: string): boolean {
  // 按 token 边界匹配，避免 SM-P615 命中 SM-P6150 之类的其它型号
  return LOW_PERF_SAMSUNG_MODELS.some((model) => {
    const escaped = model.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    return new RegExp(`(?:^|[^a-z0-9])${escaped}(?:[^a-z0-9]|$)`, 'i').test(userAgent)
  })
}

function parseOverride(search: string): boolean | null {
  const match = /[?&]perf=(low|high)\b/i.exec(search)
  if (!match) {
    return null
  }
  return match[1].toLowerCase() === 'low'
}

/**
 * 是否应进入低 GPU 档。纯函数：不写 DOM，只在 `env` 完全缺省时才读取
 * `navigator` / `window.location`。显式传入 `env` 时视作完整环境
 * （不再从全局补齐缺字段），便于在 jsdom 里精确覆盖各分支。
 */
export function isLowPerfDevice(env?: PerfEnv): boolean {
  const source: PerfEnv = env ?? readGlobalEnv()
  const userAgent = source.userAgent ?? ''
  const search = source.search ?? ''

  // 手动覆盖优先级最高：现场想让某台机器降级/不降级都走这里
  const override = parseOverride(search)
  if (override !== null) {
    return override
  }

  if (matchesSamsungModel(userAgent)) {
    return true
  }

  // memory/cores 只在 Android 上参与判定：桌面浏览器 4 核太常见，不能据此降级
  if (!/android/i.test(userAgent)) {
    return false
  }

  const memory = source.deviceMemory
  const cores = source.hardwareConcurrency

  return (
    (typeof memory === 'number' && memory <= LOW_MEMORY_GB) ||
    (typeof cores === 'number' && cores <= LOW_CORES)
  )
}

function hasLowGpu(doc: Document): boolean {
  return doc.documentElement?.hasAttribute(LOW_GPU_ATTR) ?? false
}

/**
 * 按当前环境给 `<html>` 设置/移除 `data-low-gpu`。
 * 由 main.ts 在 `app.mount` 之前调用；任何 API 缺失都静默跳过，绝不阻断启动。
 */
export function applyPerfAttributes(doc?: Document, win?: Window): void {
  try {
    const docRef = doc ?? (typeof document !== 'undefined' ? document : undefined)
    const root = docRef?.documentElement
    if (!docRef || !root) {
      return
    }

    const winRef = win ?? (typeof window !== 'undefined' ? window : undefined)
    const nav = winRef?.navigator as NavigatorWithMemory | undefined

    const low = isLowPerfDevice({
      userAgent: nav?.userAgent,
      deviceMemory: typeof nav?.deviceMemory === 'number' ? nav.deviceMemory : undefined,
      hardwareConcurrency:
        typeof nav?.hardwareConcurrency === 'number' ? nav.hardwareConcurrency : undefined,
      search: winRef?.location?.search ?? ''
    })

    if (low) {
      root.setAttribute(LOW_GPU_ATTR, '')
    } else {
      root.removeAttribute(LOW_GPU_ATTR)
    }
  } catch {
    // 探测是纯优化：失败时按普通档运行，不能影响首屏挂载
  }
}

const visibilityCleanups = new WeakMap<Document, () => void>()

/**
 * 页面不可见期间给 `<html>` 打 `data-page-hidden`，可见时移除。
 * 返回清理函数（移除监听 + 清掉属性），重复调用安全。
 */
export function installPageVisibilityFlag(doc?: Document): () => void {
  const docRef = doc ?? (typeof document !== 'undefined' ? document : undefined)
  if (!docRef) {
    return () => {}
  }

  const existing = visibilityCleanups.get(docRef)
  if (existing) {
    return existing
  }

  let installed = true

  const sync = (): void => {
    if (!installed) {
      return
    }
    const root = docRef.documentElement
    if (!root) {
      return
    }
    if (docRef.hidden) {
      root.setAttribute(PAGE_HIDDEN_ATTR, '')
    } else {
      root.removeAttribute(PAGE_HIDDEN_ATTR)
    }
  }

  const cleanup = (): void => {
    if (!installed) {
      return
    }
    installed = false
    docRef.removeEventListener('visibilitychange', sync)
    docRef.documentElement?.removeAttribute(PAGE_HIDDEN_ATTR)
    visibilityCleanups.delete(docRef)
  }

  try {
    docRef.addEventListener('visibilitychange', sync)
  } catch {
    // 极老环境没有 visibilitychange 时退化为「不做任何事」
    return () => {}
  }
  visibilityCleanups.set(docRef, cleanup)
  sync()

  return cleanup
}

/** 供样式/调试使用：当前文档是否已处于低 GPU 档。 */
export function isLowGpuActive(doc?: Document): boolean {
  const docRef = doc ?? (typeof document !== 'undefined' ? document : undefined)
  return docRef ? hasLowGpu(docRef) : false
}

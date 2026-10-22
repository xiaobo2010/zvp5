import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PlatformSignal } from '@/api/platformGuard'

// 巡检只负责「判 + 报」,上报函数单独打桩:测试不碰 axios,也不碰真实后端。
const reportSignals = vi.hoisted(() => vi.fn<(signals: PlatformSignal[]) => Promise<boolean>>())

vi.mock('@/api/platformGuard', () => ({ reportSignals }))

type Guard = typeof import('./platformGuard')

const PLAIN_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
// 学海浏览器的 UA 带 `xhbrowser`;资料中心 App 只有 `deviceId/... userId/...` 且不注入桥
const XUEHAI_UA =
  'Mozilla/5.0 (Linux; Android 13; SM-P620) AppleWebKit/537.36 Chrome/120.0 Mobile xhbrowser/1.2 deviceId/8f21ab userId/9527'
const DATACENTER_UA =
  'Mozilla/5.0 (Linux; Android 13; SM-P620) AppleWebKit/537.36 Chrome/120.0 Mobile deviceId/8f21ab userId/9527'

interface SavedProp {
  target: object
  name: string
  descriptor: PropertyDescriptor | undefined
}

const savedProps: SavedProp[] = []

function setProp(target: object, name: string, value: unknown) {
  savedProps.push({ target, name, descriptor: Object.getOwnPropertyDescriptor(target, name) })
  Object.defineProperty(target, name, { configurable: true, writable: true, value })
}

function restoreProps() {
  while (savedProps.length > 0) {
    const saved = savedProps.pop()
    if (!saved) {
      break
    }
    if (saved.descriptor) {
      Object.defineProperty(saved.target, saved.name, saved.descriptor)
    } else {
      delete (saved.target as Record<string, unknown>)[saved.name]
    }
  }
}

/** 生产页面的两个标记都存在;缺了才算 tamper。 */
function installPlatformMarkup() {
  const app = document.createElement('div')
  app.id = 'app'
  document.body.appendChild(app)
  const meta = document.createElement('meta')
  meta.name = 'zvms-platform'
  meta.content = '1'
  document.head.appendChild(meta)
}

function setWindowSize(outer: number, inner: number) {
  setProp(window, 'outerWidth', outer)
  setProp(window, 'innerWidth', inner)
  setProp(window, 'outerHeight', inner)
  setProp(window, 'innerHeight', inner)
}

async function loadGuard(): Promise<Guard> {
  return await import('./platformGuard')
}

async function tick(ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
}

function reportedSignals(call = 0): PlatformSignal[] {
  return reportSignals.mock.calls[call][0]
}

function reportedNames(call = 0): string[] {
  return reportedSignals(call).map((item) => item.signal)
}

beforeEach(() => {
  // 模块内藏着 devtools 连续计数与结果缓存,每个用例都要一份干净状态
  vi.resetModules()
  reportSignals.mockClear()
  reportSignals.mockResolvedValue(true)
  localStorage.clear()
  document.body.innerHTML = ''
  document.head.querySelectorAll('meta[name="zvms-platform"]').forEach((node) => node.remove())
  installPlatformMarkup()
  setProp(navigator, 'userAgent', PLAIN_UA)
  delete window.xhBrowserJava
  setWindowSize(1024, 1024)
  vi.useFakeTimers({ now: new Date('2026-01-01T08:00:00Z') })
  localStorage.setItem('token', 'test-token')
})

afterEach(() => {
  vi.useRealTimers()
  restoreProps()
  localStorage.clear()
})

describe('checkPlatform 信号判定', () => {
  it('被 iframe 嵌套时报 iframe', async () => {
    setProp(window, 'top', { name: 'parent-window' })
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedSignals()).toEqual([{ signal: 'iframe', detail: 'top!=self' }])
  })

  it('学海 UA 但没有可用桥时报 bridge', async () => {
    setProp(navigator, 'userAgent', XUEHAI_UA)
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedSignals()).toEqual([
      { signal: 'bridge', detail: 'xhBrowserJava.getUserId unavailable' }
    ])
  })

  it('桥存在但 getUserId 抛异常同样算桥不可用', async () => {
    setProp(navigator, 'userAgent', XUEHAI_UA)
    window.xhBrowserJava = {
      getUserId() {
        throw new Error('bridge broken')
      }
    }
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportedNames()).toEqual(['bridge'])
  })

  it('普通 PC 浏览器即使没有桥也不报', async () => {
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('资料中心 App(UA 只有 userId、本就不注入桥)不报 bridge', async () => {
    setProp(navigator, 'userAgent', DATACENTER_UA)
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('未登录(token 缺失)时不检测也不上报', async () => {
    localStorage.removeItem('token')
    setProp(window, 'top', { name: 'parent-window' })
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('页面不可见时不采样不上报', async () => {
    setProp(document, 'visibilityState', 'hidden')
    setProp(window, 'top', { name: 'parent-window' })
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })
})

describe('checkPlatform devtools 判定', () => {
  it('连续 3 次采样命中才报 devtools', async () => {
    setWindowSize(1300, 1024)
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals()[0].detail).toContain('devtools:width+276')
  })

  it('中间一次未命中会清零连续计数', async () => {
    setWindowSize(1300, 1024)
    const { checkPlatform } = await loadGuard()
    await checkPlatform()
    await tick(60_000)
    await checkPlatform()

    // 恢复正常窗口尺寸:命中链断掉,后面两次仍不够 3 连
    setWindowSize(1024, 1024)
    await tick(60_000)
    await checkPlatform()
    setWindowSize(1300, 1024)
    await tick(60_000)
    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('平台标记 meta 被摘掉时按 devtools(tamper)上报', async () => {
    document.head.querySelector('meta[name="zvms-platform"]')?.remove()
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 3; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals()[0].detail).toBe('tamper:meta-missing')
  })
})

describe('checkPlatform 节流', () => {
  it('普通信号 5 分钟内只上报一次,窗口过后可再报', async () => {
    setProp(window, 'top', { name: 'parent-window' })
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('zvms.platformGuard.report.iframe')).toBeTruthy()

    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)

    // 距首次上报满 5 分钟(throttle 与后端去重窗口对齐)才允许补报
    await tick(4 * 60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(2)
  })

  it('devtools 节流 30 分钟,普通窗口过了也不重报', async () => {
    setWindowSize(1300, 1024)
    const { checkPlatform } = await loadGuard()
    for (let i = 0; i < 3; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }
    expect(reportSignals).toHaveBeenCalledTimes(1)

    // 再来一轮 3 连命中(共 3 分钟),仍在 30 分钟窗口内
    for (let i = 0; i < 3; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }
    expect(reportSignals).toHaveBeenCalledTimes(1)
  })
})

describe('startPlatformGuard / stopPlatformGuard', () => {
  it('重复启动只保留一个定时器,停止后不再巡检', async () => {
    setWindowSize(1300, 1024)
    const { startPlatformGuard, stopPlatformGuard } = await loadGuard()

    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval')
    startPlatformGuard()
    startPlatformGuard()
    // 幂等:第二次启动只该复用已有定时器
    expect(setIntervalSpy).toHaveBeenCalledTimes(1)
    // 启动即巡检一次,定时器再造两次(共 3 次采样)
    await tick(120_000)
    expect(reportSignals).toHaveBeenCalledTimes(1)

    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
    stopPlatformGuard()
    expect(clearIntervalSpy).toHaveBeenCalledTimes(1)
    setWindowSize(1300, 1024)
    await tick(180_000)
    expect(reportSignals).toHaveBeenCalledTimes(1)
  })
})

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

/** jsdom 默认 origin 是 localhost */
const SELF_HOST = 'localhost'
const PORTAL_REF = 'https://portal.school.edu/course'

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

/** 直接设定「窗口被压缩的宽/高差」,用来精确构造 weak / 单条件场景。 */
function setWindowGaps(widthGap: number, heightGap: number) {
  setProp(window, 'outerWidth', 1024 + widthGap)
  setProp(window, 'innerWidth', 1024)
  setProp(window, 'outerHeight', 1024 + heightGap)
  setProp(window, 'innerHeight', 1024)
}

interface DebuggerTimer {
  /** 设置下一次采样里 debugger 的挂起时长(ms)。 */
  setCost(ms: number): void
  /** 复位配对计数,保证下一次采样的两次 now() 调用分别落在奇/偶位。 */
  reset(): void
}

/**
 * debugger 计时桩:模块在 debugger 前后各取一次 performance.now(),
 * 这里把第二次调用的时间往后推 ms 毫秒,模拟主线程被挂起。
 * 不用真的 debugger 语句,免得打断测试进程。
 */
function stubDebuggerCost(initialMs: number): DebuggerTimer {
  let call = 0
  let cost = initialMs
  const realNow = performance.now.bind(performance)
  setProp(window, 'performance', {
    now: () => {
      call += 1
      return call % 2 === 1 ? realNow() : realNow() + cost
    }
  })
  return {
    setCost(ms: number) {
      cost = ms
    },
    reset() {
      call = 0
    }
  }
}

/** 采样一次:先固定本次 debugger 挂起时长并复位配对,再巡检。 */
async function sampleWith(
  checkPlatform: () => Promise<void>,
  timer: DebuggerTimer,
  cost: number
): Promise<void> {
  timer.setCost(cost)
  timer.reset()
  await checkPlatform()
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

function embedCrossOrigin() {
  setProp(window, 'top', { name: 'parent-window' })
  setProp(document, 'referrer', PORTAL_REF)
}

function embedSelf() {
  setProp(window, 'top', { name: 'parent-window' })
  setProp(document, 'referrer', `https://${SELF_HOST}/course`)
}

beforeEach(() => {
  // 模块内藏着各会话状态与节流,每个用例都要一份干净状态
  vi.resetModules()
  reportSignals.mockClear()
  reportSignals.mockResolvedValue(true)
  localStorage.clear()
  document.body.innerHTML = ''
  document.head.querySelectorAll('meta[name="zvms-platform"]').forEach((node) => node.remove())
  installPlatformMarkup()
  setProp(navigator, 'userAgent', PLAIN_UA)
  setProp(navigator, 'language', 'zh-CN')
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

describe('iframe 判定', () => {
  it('正常嵌入(同源 referrer 舰名)不上报', async () => {
    embedSelf()
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 6; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('referrer 为空(正常门户/投屏常见)不上报', async () => {
    setProp(window, 'top', { name: 'parent-window' })
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 6; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('平台自身(未嵌套)不检测,直接跳过', async () => {
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 6; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('跨域嵌满 120 秒才报一次,带 pathname 与 referrer 主机名', async () => {
    embedCrossOrigin()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    // 持续嵌套满 120 秒:第三次巡检起报,且只报一次
    await tick(60_000)
    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['iframe'])
    const detail = reportedSignals()[0].detail as string
    expect(detail).toContain(`path=${location.pathname}`)
    expect(detail).toContain('ref=portal.school.edu')
    expect(detail).not.toContain('?')
  })
})

describe('bridge 判定', () => {
  it('普通 PC 浏览器即使没有桥也不报', async () => {
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 6; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('资料中心 App(UA 只有 userId、本就不注入桥)不报 bridge', async () => {
    setProp(navigator, 'userAgent', DATACENTER_UA)
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 6; i += 1) {
      await checkPlatform()
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('连续 3 次探测失败后才报 bridge,且距首次缺失 >= 30 秒', async () => {
    setProp(navigator, 'userAgent', XUEHAI_UA)
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    // 第二次探测只累计为 2 连败
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    // 第三次探测:累计 3 连败,且距首次缺失超过 30 秒
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['bridge'])
    expect(reportedSignals(0)[0].detail).toContain('version=1.2')
  })

  it('桥中途恢复即清零,之后重新失败要重新累计', async () => {
    setProp(navigator, 'userAgent', XUEHAI_UA)
    let usable = false
    window.xhBrowserJava = {
      getUserId: () => {
        if (!usable) {
          throw new Error('not ready')
        }
        return 9527
      }
    } as never
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    // 桥恢复:失败计数清零
    usable = true
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    usable = false
    // 重新失败两次不足以构成 3 连败
    await tick(60_000)
    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).not.toHaveBeenCalled()

    await tick(60_000)
    await checkPlatform()
    expect(reportedNames()).toEqual(['bridge'])
  })
})

describe('devtools 判定', () => {
  it('单条件(只有宽度差)不报,即使放宽后仍超过 240px', async () => {
    setWindowGaps(376, 0)
    const timer = stubDebuggerCost(0)
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 10; i += 1) {
      await sampleWith(checkPlatform, timer, 0)
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('单条件(只有高度差)不报', async () => {
    setWindowGaps(0, 376)
    const timer = stubDebuggerCost(0)
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 10; i += 1) {
      await sampleWith(checkPlatform, timer, 0)
      await tick(60_000)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('strong(debugger 挂起 >300ms)连续 3 个采样周期即报', async () => {
    setWindowGaps(0, 0)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    expect(reportSignals).not.toHaveBeenCalled()

    // 第三个采样周期:strong 连击满 3
    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals()[0].detail).toBe('panel-detected')
  })

  it('debugger 只连续 2 个采样周期不报', async () => {
    setWindowGaps(0, 0)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('weak(宽高双向压缩)连续 5 个采样周期才报', async () => {
    setWindowGaps(376, 376)
    const timer = stubDebuggerCost(0)
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 4; i += 1) {
      await sampleWith(checkPlatform, timer, 0)
      await tick(60_000)
      expect(reportSignals).not.toHaveBeenCalled()
    }

    // 第五个采样周期:weak 连击满 5
    await sampleWith(checkPlatform, timer, 0)
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals()[0].detail).toBe('panel-detected')
  })

  it('同一采样周期 strong 与 weak 同时成立时按 strong 走(3 连就报)', async () => {
    setWindowGaps(376, 376)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    for (let i = 0; i < 2; i += 1) {
      await sampleWith(checkPlatform, timer, 400)
      await tick(60_000)
      expect(reportSignals).not.toHaveBeenCalled()
    }

    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).toHaveBeenCalledTimes(1)
  })

  it('strong 命中后 weak 计数不影响(前置 weak 不提前上报)', async () => {
    setWindowGaps(376, 376)
    const timer = stubDebuggerCost(0)
    const { checkPlatform } = await loadGuard()

    // 前两个采样周期只有 weak 成立(窗口被压缩、无 debugger 挂起)
    for (let i = 0; i < 2; i += 1) {
      await sampleWith(checkPlatform, timer, 0)
      await tick(60_000)
      expect(reportSignals).not.toHaveBeenCalled()
    }

    // 接着三个采样周期 strong 成立:weak 计数不参与,按 strong 的 3 连上报
    for (let i = 0; i < 2; i += 1) {
      await sampleWith(checkPlatform, timer, 400)
      await tick(60_000)
      expect(reportSignals).not.toHaveBeenCalled()
    }
    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
  })

  it('两者都不命中时连击清零', async () => {
    setWindowGaps(0, 0)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    // 先攒 2 次 strong
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    expect(reportSignals).not.toHaveBeenCalled()

    // 一次都不命中(无挂起、窗口正常):两个连击计数一并清零
    await sampleWith(checkPlatform, timer, 0)
    await tick(60_000)

    // 再攒 2 次 strong:连击从头开始,仍不足 3 连
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('页面隐藏时不累计连击', async () => {
    setWindowGaps(0, 0)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    setProp(document, 'hidden', true)
    setProp(document, 'visibilityState', 'hidden')
    for (let i = 0; i < 5; i += 1) {
      await sampleWith(checkPlatform, timer, 400)
      await tick(60_000)
    }
    expect(reportSignals).not.toHaveBeenCalled()

    // 回到前台:连击从零重新累计,仍要 3 连才报
    setProp(document, 'hidden', false)
    setProp(document, 'visibilityState', 'visible')
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).not.toHaveBeenCalled()

    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).toHaveBeenCalledTimes(1)
  })

  it('中间一次未命中会清零连续计数', async () => {
    setWindowGaps(376, 376)
    const timer = stubDebuggerCost(0)
    const { checkPlatform } = await loadGuard()
    for (let i = 0; i < 4; i += 1) {
      await sampleWith(checkPlatform, timer, 0)
      await tick(60_000)
    }

    // 恢复正常窗口尺寸:命中链断掉,后面几次仍不足 5 连
    setWindowGaps(0, 0)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 0)
    setWindowGaps(376, 376)
    for (let i = 0; i < 3; i += 1) {
      await tick(60_000)
      await sampleWith(checkPlatform, timer, 0)
    }

    expect(reportSignals).not.toHaveBeenCalled()
  })
})

describe('tamper 判定(检测强度收敛版,沿用 devtools 信号名)', () => {
  it('meta 被摘时在页面加载 10 秒后单独上报一次,不再要求连续命中', async () => {
    document.head.querySelector('meta[name="zvms-platform"]')?.remove()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals(0)[0].detail).toBe('tamper:meta-missing')
  })

  it('#app 被摘时同样上报,且各自限一次/会话', async () => {
    document.body.querySelector('#app')?.remove()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)
    expect(reportedNames()).toEqual(['devtools'])
    expect(reportedSignals(0)[0].detail).toBe('tamper:app-missing')

    // 伪装恢复再重复摘:不重复上报
    installPlatformMarkup()
    for (let i = 0; i < 4; i += 1) {
      await tick(60_000)
      await checkPlatform()
    }
    expect(reportSignals).toHaveBeenCalledTimes(1)
  })

  it('readyState 未 complete 时不判定', async () => {
    setProp(document, 'readyState', 'loading')
    document.head.querySelector('meta[name="zvms-platform"]')?.remove()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()
    await tick(120_000)
    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })
})

describe('按键隔离与批量语义', () => {
  it('切账号不继承节流:A 刚上报过,B 在 10 分钟内仍按自身节奏上报', async () => {
    embedCrossOrigin()
    const { checkPlatform, setPlatformGuardAccount } = await loadGuard()
    setPlatformGuardAccount('1001')
    localStorage.setItem('token', 'test-token')

    // 账号 A:持续嵌套满 120 秒后上报一次
    await checkPlatform()
    await tick(120_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(1)

    // 账号 B 在 A 上报后 < 10 分钟切换进来:不继承 A 的节流
    setPlatformGuardAccount('1002')
    localStorage.setItem('token', 'test-token')
    await checkPlatform()
    await tick(120_000)
    await checkPlatform()
    expect(reportSignals).toHaveBeenCalledTimes(2)
    expect(reportedNames(0)).toEqual(['iframe'])
    expect(reportedNames(1)).toEqual(['iframe'])
  })

  it('登出清理当前账号的键;不同账号各自独立累计', async () => {
    embedCrossOrigin()
    const { checkPlatform, setPlatformGuardAccount, clearPlatformGuardData } = await loadGuard()
    setPlatformGuardAccount('1001')
    localStorage.setItem('token', 'test-token')

    await checkPlatform()
    // 嵌套候选的计时戳已写入 A 名下
    expect(localStorage.getItem('zvms.platformGuard.1001.iframe.firstAt')).toBeTruthy()

    await tick(120_000)
    await checkPlatform()
    expect(localStorage.getItem('zvms.platformGuard.1001.report.iframe')).toBeTruthy()

    // 登出:该账号全部巡检键被清理
    clearPlatformGuardData()
    expect(localStorage.getItem('zvms.platformGuard.1001.report.iframe')).toBeFalsy()
    expect(localStorage.getItem('zvms.platformGuard.1001.iframe.firstAt')).toBeFalsy()
  })

  it('一次上报最多携带 2 个信号,不为凑数重复发送', async () => {
    embedCrossOrigin()
    setProp(navigator, 'userAgent', XUEHAI_UA)
    document.head.querySelector('meta[name="zvms-platform"]')?.remove()
    const { checkPlatform, setPlatformGuardAccount } = await loadGuard()
    setPlatformGuardAccount('1001')
    localStorage.setItem('token', 'test-token')

    // 先让 iframe / bridge 的时长条件“烧至就绪”,最后一步再摘 meta,
    // 三路信号在同一巡检同时命中
    await checkPlatform()
    await tick(60_000)
    await checkPlatform()
    document.head.querySelector('meta[name="zvms-platform"]')?.remove()
    await tick(60_000)
    await checkPlatform()

    expect(reportSignals).toHaveBeenCalledTimes(2)
    expect(reportedSignals(1).length).toBe(2)
    expect(reportedNames(1).some((name) => ['iframe', 'bridge'].includes(name))).toBe(true)
  })

  it('同一信号 10 分钟内不重复(devtools 重奏被压住,过窗可再报)', async () => {
    setWindowGaps(0, 0)
    const timer = stubDebuggerCost(400)
    const { checkPlatform } = await loadGuard()

    // strong 连击 3 个采样周期:第三次上报(t=120s)
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)
    await tick(60_000)
    await sampleWith(checkPlatform, timer, 400)
    expect(reportSignals).toHaveBeenCalledTimes(1)

    // 节流窗口内继续攒连击:每次攒满 3 连都会被压住,不重复报
    for (let i = 0; i < 9; i += 1) {
      await tick(60_000)
      await sampleWith(checkPlatform, timer, 400)
    }
    expect(reportSignals).toHaveBeenCalledTimes(1)

    // 距首报满 10 分钟(>=600s)后再攒满 3 连可再报
    for (let i = 0; i < 3; i += 1) {
      await tick(60_000)
      await sampleWith(checkPlatform, timer, 400)
    }
    expect(reportSignals).toHaveBeenCalledTimes(2)
  })
})

describe('登录态与可见性前置', () => {
  it('未登录(token 缺失)时不检测也不上报', async () => {
    localStorage.removeItem('token')
    embedCrossOrigin()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })

  it('页面不可见时不采样不上报', async () => {
    setProp(document, 'visibilityState', 'hidden')
    embedCrossOrigin()
    const { checkPlatform } = await loadGuard()

    await checkPlatform()

    expect(reportSignals).not.toHaveBeenCalled()
  })
})

describe('startPlatformGuard / stopPlatformGuard', () => {
  it('重复启动只保留一个定时器,停止后不再巡检', async () => {
    const { startPlatformGuard, stopPlatformGuard } = await loadGuard()

    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval')
    startPlatformGuard()
    startPlatformGuard()
    // 幂等:第二次启动只该复用已有定时器
    expect(setIntervalSpy).toHaveBeenCalledTimes(1)

    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
    stopPlatformGuard()
    expect(clearIntervalSpy).toHaveBeenCalledTimes(1)
    await tick(180_000)
    expect(reportSignals).not.toHaveBeenCalled()
  })
})

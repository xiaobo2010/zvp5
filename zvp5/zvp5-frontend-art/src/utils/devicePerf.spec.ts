import { afterEach, describe, expect, it } from 'vitest'

import { applyPerfAttributes, installPageVisibilityFlag, isLowPerfDevice } from './devicePerf'

const SAMSUNG_UA = (model: string) =>
  `Mozilla/5.0 (Linux; Android 12; ${model}) AppleWebKit/537.36 (KHTML, like Gecko) ` +
  'Chrome/110.0.0.0 Safari/537.36'

const ANDROID_UA =
  'Mozilla/5.0 (Linux; Android 11; Pixel 4a) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/120.0.0.0 Mobile Safari/537.36'

const DESKTOP_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/120.0.0.0 Safari/537.36'

const SAMSUNG_MODELS = ['SM-P620', 'SM-P615C', 'SM-P615', 'SM-P200', 'SM-P355C']

afterEach(() => {
  document.documentElement.removeAttribute('data-low-gpu')
  document.documentElement.removeAttribute('data-page-hidden')
  delete (document as unknown as Record<string, unknown>).hidden
})

describe('isLowPerfDevice 低 GPU 判定', () => {
  it('已知低端三星型号一律命中', () => {
    SAMSUNG_MODELS.forEach((model) => {
      expect(isLowPerfDevice({ userAgent: SAMSUNG_UA(model) })).toBe(true)
    })
  })

  it('相近但不同的型号不被误判', () => {
    expect(isLowPerfDevice({ userAgent: SAMSUNG_UA('SM-P6150') })).toBe(false)
    expect(isLowPerfDevice({ userAgent: SAMSUNG_UA('SM-P620X') })).toBe(false)
  })

  it('Android + 低内存 / 低核心数命中', () => {
    expect(isLowPerfDevice({ userAgent: ANDROID_UA, deviceMemory: 4 })).toBe(true)
    expect(isLowPerfDevice({ userAgent: ANDROID_UA, hardwareConcurrency: 4 })).toBe(true)
  })

  it('Android 但内存与核心都充足时不降级', () => {
    expect(
      isLowPerfDevice({ userAgent: ANDROID_UA, deviceMemory: 8, hardwareConcurrency: 8 })
    ).toBe(false)
  })

  it('桌面浏览器即使 4 核 4GB 也不降级', () => {
    expect(
      isLowPerfDevice({ userAgent: DESKTOP_UA, deviceMemory: 4, hardwareConcurrency: 4 })
    ).toBe(false)
  })

  it('缺少能力值时不做猜测', () => {
    expect(isLowPerfDevice({ userAgent: ANDROID_UA })).toBe(false)
  })

  it('?perf=low / ?perf=high 覆盖自动判定', () => {
    expect(isLowPerfDevice({ userAgent: DESKTOP_UA, search: '?perf=low' })).toBe(true)
    expect(isLowPerfDevice({ userAgent: SAMSUNG_UA('SM-P620'), search: '?perf=high' })).toBe(false)
    // 与其它参数共存时仍生效
    expect(isLowPerfDevice({ userAgent: DESKTOP_UA, search: '?from=qr&perf=low' })).toBe(true)
  })
})

describe('applyPerfAttributes 写 DOM 属性', () => {
  const fakeWindow = (userAgent: string, search = '') =>
    ({ navigator: { userAgent }, location: { search } }) as unknown as Window

  it('命中低端设备时设置 data-low-gpu', () => {
    applyPerfAttributes(document, fakeWindow(SAMSUNG_UA('SM-P620')))
    expect(document.documentElement.hasAttribute('data-low-gpu')).toBe(true)
  })

  it('普通设备上移除遗留的 data-low-gpu', () => {
    document.documentElement.setAttribute('data-low-gpu', '')
    applyPerfAttributes(document, fakeWindow(DESKTOP_UA))
    expect(document.documentElement.hasAttribute('data-low-gpu')).toBe(false)
  })

  it('查询参数覆盖生效', () => {
    applyPerfAttributes(document, fakeWindow(DESKTOP_UA, '?perf=low'))
    expect(document.documentElement.hasAttribute('data-low-gpu')).toBe(true)
    applyPerfAttributes(document, fakeWindow(SAMSUNG_UA('SM-P620'), '?perf=high'))
    expect(document.documentElement.hasAttribute('data-low-gpu')).toBe(false)
  })

  it('API 缺失时不抛错', () => {
    expect(() => applyPerfAttributes({} as Document, {} as Window)).not.toThrow()
  })
})

describe('installPageVisibilityFlag 页面可见性标志', () => {
  function setHidden(value: boolean): void {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => value
    })
  }

  it('不可见时打标志，可见时移除', () => {
    setHidden(false)
    const cleanup = installPageVisibilityFlag(document)
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(false)

    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.documentElement.getAttribute('data-page-hidden')).toBe('')

    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(false)

    cleanup()
  })

  it('清理后移除监听与遗留标志，重复清理安全', () => {
    setHidden(true)
    const cleanup = installPageVisibilityFlag(document)
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(true)

    cleanup()
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(false)

    // 监听已摘除：再次进入后台不会重新打标志
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(false)

    expect(() => cleanup()).not.toThrow()
  })

  it('重复安装不叠加监听（幂等）', () => {
    setHidden(false)
    const first = installPageVisibilityFlag(document)
    const second = installPageVisibilityFlag(document)

    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(true)

    second()
    // 第一次的句柄与第二次是同一个：清理后不再响应
    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.documentElement.hasAttribute('data-page-hidden')).toBe(false)

    expect(() => first()).not.toThrow()
  })
})

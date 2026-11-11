import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'

import { prefetchRoute, prefetchRoutesWhenIdle, warmupFonts } from './routePrefetch'

/* prefetchedPaths 是模块级 Set（保证运行时幂等），测试间会互相串味：
   给每个用例一个独立的路由命名空间，互不重叠。 */
let seq = 0

function makeRouter() {
  const root = `/probe-${++seq}`
  const factory = vi.fn(() => Promise.resolve(defineComponent({ name: 'LazyProbe' })))
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: `${root}/user`, component: factory },
      { path: `${root}/eager`, component: defineComponent({ name: 'PlainProbe' }) },
      { path: `${root}/nested`, children: [{ path: 'child', component: factory }] }
    ]
  })
  return { router, factory, root }
}

/* 某个用例失败后也要把 fake timers / stub 全局恢复，避免污染后续用例
   （真实翻过车：断言抛错导致 useRealTimers 未执行，下一个用例的
   setTimeout 永远不触发，整组超时）。 */
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('prefetchRoute 路由 chunk 预取', () => {
  it('懒加载工厂被调用（触发 chunk 下载）', () => {
    const { router, factory, root } = makeRouter()
    prefetchRoute(router, `${root}/user`)
    expect(factory).toHaveBeenCalledTimes(1)
  })

  it('同一路径重复预取被 Set 短路', () => {
    const { router, factory, root } = makeRouter()
    const path = `${root}/user`
    prefetchRoute(router, path)
    prefetchRoute(router, path)
    expect(factory).toHaveBeenCalledTimes(1)
  })

  it('嵌套路由逐层预取', () => {
    const { router, factory, root } = makeRouter()
    prefetchRoute(router, `${root}/nested/child`)
    // 父记录无组件、子记录为懒加载工厂 → 工厂恰好被调用一次
    expect(factory).toHaveBeenCalledTimes(1)
  })

  it('已解析的非函数组件安全跳过', () => {
    const { router, factory, root } = makeRouter()
    expect(() => prefetchRoute(router, `${root}/eager`)).not.toThrow()
    expect(factory).not.toHaveBeenCalled()
  })

  it('未注册路径不抛错', () => {
    const { router } = makeRouter()
    expect(() => prefetchRoute(router, '/__not_registered__')).not.toThrow()
  })
})

describe('prefetchRoutesWhenIdle 空闲批量预取', () => {
  it('requestIdleCallback 可用时走 idle 通道', () => {
    const idle = vi.fn((cb: IdleRequestCallback) => {
      cb({ didTimeout: false, timeRemaining: () => 0 })
      return 1
    })
    vi.stubGlobal('requestIdleCallback', idle)
    const { router, factory, root } = makeRouter()

    prefetchRoutesWhenIdle(router, [`${root}/user`, `${root}/eager`])
    expect(idle).toHaveBeenCalledTimes(1)
    expect(factory).toHaveBeenCalledTimes(1)
  })

  it('省流模式（saveData）不后台预取', () => {
    const idle = vi.fn()
    vi.stubGlobal('requestIdleCallback', idle)
    const connection = { saveData: true, effectiveType: '4g' }
    vi.stubGlobal('navigator', { ...navigator, connection })
    const { router, factory, root } = makeRouter()

    prefetchRoutesWhenIdle(router, [`${root}/user`])
    expect(idle).not.toHaveBeenCalled()
    expect(factory).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('2G 弱网不后台预取', () => {
    const idle = vi.fn()
    vi.stubGlobal('requestIdleCallback', idle)
    const connection = { saveData: false, effectiveType: '2g' }
    vi.stubGlobal('navigator', { ...navigator, connection })
    const { router, factory, root } = makeRouter()

    prefetchRoutesWhenIdle(router, [`${root}/user`])
    expect(idle).not.toHaveBeenCalled()
    expect(factory).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('无 requestIdleCallback 时退化为 setTimeout', () => {
    vi.stubGlobal('requestIdleCallback', undefined)
    vi.useFakeTimers()
    const { router, factory, root } = makeRouter()

    prefetchRoutesWhenIdle(router, [`${root}/user`])
    expect(factory).not.toHaveBeenCalled()
    vi.advanceTimersByTime(300)
    expect(factory).toHaveBeenCalledTimes(1)
  })
})

describe('warmupFonts 字体预热', () => {
  it('document.fonts 缺失时静默', () => {
    expect(() => warmupFonts(['400 1em Bebas'])).not.toThrow()
  })

  it('有 fonts API 时逐个 load 且吞掉错误', async () => {
    const load = vi.fn(() => Promise.reject(new Error('boom')))
    const descriptor = {
      value: { load },
      configurable: true
    }
    const originalDescriptor = Object.getOwnPropertyDescriptor(document, 'fonts')
    Object.defineProperty(document, 'fonts', descriptor)

    expect(() =>
      warmupFonts(['400 1em Bebas', '700 1em Rajdhani'])
    ).not.toThrow()
    expect(load).toHaveBeenCalledTimes(2)

    // 走一个微任务节拍，确认 rejection 被吞掉而不是 unhandled
    await Promise.resolve().then(() => {})

    if (originalDescriptor) Object.defineProperty(document, 'fonts', originalDescriptor)
    else delete (document as unknown as { fonts?: unknown }).fonts
  })
})

/**
 * vitest 全局 setup：补 jsdom 缺失的浏览器 API。
 *
 * jsdom 没有 matchMedia 与 Web Animations API，而基础件（LiquidGlass、ZPagination、
 * useCountUp 等）默认它们存在。测试里没有真实的媒体查询与动画，这里给最小实现：
 * 媒体查询恒不命中（走无动画 / 浅色路径），animate() 返回一个可 cancel 的空动画对象。
 */

type MediaQueryListener = (event: MediaQueryListEvent) => void

function createMediaQueryList(query: string): MediaQueryList {
  const listeners = new Set<MediaQueryListener>()
  return {
    matches: false,
    media: query,
    onchange: null,
    addListener: (listener: MediaQueryListener | null) => {
      if (listener) listeners.add(listener)
    },
    removeListener: (listener: MediaQueryListener | null) => {
      if (listener) listeners.delete(listener)
    },
    addEventListener: (_type: string, listener: MediaQueryListener | null) => {
      if (listener) listeners.add(listener)
    },
    removeEventListener: (_type: string, listener: MediaQueryListener | null) => {
      if (listener) listeners.delete(listener)
    },
    dispatchEvent: () => false
  } as unknown as MediaQueryList
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => createMediaQueryList(query)
  })
}

if (typeof globalThis.ResizeObserver !== 'function') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

if (typeof Element !== 'undefined' && typeof Element.prototype.animate !== 'function') {
  const noopAnimation = {
    cancel: () => {},
    finish: () => {},
    play: () => {},
    pause: () => {},
    reverse: () => {},
    commitStyles: () => {},
    finished: Promise.resolve(),
    onfinish: null,
    oncancel: null
  }
  Object.defineProperty(Element.prototype, 'animate', {
    writable: true,
    configurable: true,
    value: () => noopAnimation
  })
}

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  installScrollActivityFlag,
  uninstallScrollActivityFlag
} from './scrollActivity'

describe('scrollActivity 滚动状态标志', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    installScrollActivityFlag()
  })

  afterEach(() => {
    uninstallScrollActivityFlag()
    vi.useRealTimers()
  })

  it('内部容器滚动（捕获阶段）时给 <html> 打 data-scrolling', () => {
    const inner = document.createElement('div')
    document.body.appendChild(inner)

    inner.dispatchEvent(new Event('scroll', { bubbles: true }))
    expect(document.documentElement.dataset.scrolling).toBe('')

    inner.remove()
  })

  it('静止 160ms 后自动移除标志', () => {
    window.dispatchEvent(new Event('scroll'))
    expect(document.documentElement.dataset.scrolling).toBe('')

    vi.advanceTimersByTime(160)
    expect(document.documentElement.dataset.scrolling).toBeUndefined()
  })

  it('连续滚动只翻转一次标志，不闪断', () => {
    for (let i = 0; i < 5; i++) {
      window.dispatchEvent(new Event('scroll'))
      vi.advanceTimersByTime(50)
    }
    expect(document.documentElement.dataset.scrolling).toBe('')

    vi.advanceTimersByTime(160)
    expect(document.documentElement.dataset.scrolling).toBeUndefined()
  })

  it('卸载后不再响应滚动，且清掉遗留标志', () => {
    window.dispatchEvent(new Event('scroll'))
    expect(document.documentElement.dataset.scrolling).toBe('')

    uninstallScrollActivityFlag()
    window.dispatchEvent(new Event('scroll'))
    expect(document.documentElement.dataset.scrolling).toBeUndefined()
  })
})

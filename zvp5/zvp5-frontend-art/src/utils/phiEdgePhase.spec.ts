import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { startPhiEdgePhase, stopPhiEdgePhase } from './phiEdgePhase'

type IOCallback = (entries: IntersectionObserverEntry[], observer: IntersectionObserver) => void

class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []

  readonly options: IntersectionObserverInit | undefined
  targets = new Set<Element>()
  disconnected = false

  constructor(
    private readonly callback: IOCallback,
    options?: IntersectionObserverInit
  ) {
    this.options = options
    FakeIntersectionObserver.instances.push(this)
  }

  observe(el: Element): void {
    this.targets.add(el)
  }

  unobserve(el: Element): void {
    this.targets.delete(el)
  }

  disconnect(): void {
    this.disconnected = true
    this.targets.clear()
  }

  takeRecords(): IntersectionObserverEntry[] {
    return []
  }

  /** 测试辅助：手动触发一次可见性回调 */
  fire(el: Element, isIntersecting: boolean): void {
    this.callback(
      [{ target: el, isIntersecting } as unknown as IntersectionObserverEntry],
      this as unknown as IntersectionObserver
    )
  }
}

class FakeMutationObserver {
  static instances: FakeMutationObserver[] = []

  disconnected = false

  constructor(private readonly callback: MutationCallback) {
    FakeMutationObserver.instances.push(this)
  }

  observe(): void {}

  disconnect(): void {
    this.disconnected = true
  }

  takeRecords(): MutationRecord[] {
    return []
  }

  /** 测试辅助：模拟一次 DOM 变更后的防抖盖章 */
  trigger(): void {
    this.callback([], this as unknown as MutationObserver)
  }
}

function mountCard(): HTMLElement {
  const shell = document.createElement('div')
  shell.className = 'yg-phi'
  const card = document.createElement('div')
  card.className = 'el-card'
  shell.appendChild(card)
  document.body.appendChild(shell)
  return card
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
  vi.stubGlobal('MutationObserver', FakeMutationObserver)
  FakeIntersectionObserver.instances = []
  FakeMutationObserver.instances = []
})

afterEach(() => {
  stopPhiEdgePhase()
  document.documentElement.removeAttribute('data-low-gpu')
  document.body.innerHTML = ''
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('phiEdgePhase 流光相位与屏外暂停', () => {
  it('启动时给卡片写一次随机负相位，并交给 IntersectionObserver 观察', () => {
    const card = mountCard()

    startPhiEdgePhase()

    expect(card.getAttribute('data-phi-edge-phase')).toBe('')
    expect(card.style.getPropertyValue('--phi-edge-delay')).toMatch(/^-\d+ms$/)

    const io = FakeIntersectionObserver.instances[0]
    expect(io).toBeDefined()
    expect(io.options?.rootMargin).toBe('200px 0px')
    expect(io.targets.has(card)).toBe(true)
  })

  it('相位只写一次：重复启动不改变已有 delay', () => {
    const card = mountCard()
    startPhiEdgePhase()
    const first = card.style.getPropertyValue('--phi-edge-delay')

    stopPhiEdgePhase()
    startPhiEdgePhase()

    expect(card.style.getPropertyValue('--phi-edge-delay')).toBe(first)
  })

  it('屏外的框打 data-phi-edge-idle，回到视口时移除', () => {
    const card = mountCard()
    startPhiEdgePhase()
    const io = FakeIntersectionObserver.instances[0]

    io.fire(card, false)
    expect(card.getAttribute('data-phi-edge-idle')).toBe('')

    io.fire(card, true)
    expect(card.hasAttribute('data-phi-edge-idle')).toBe(false)
  })

  it('新插入的卡片（防抖后）也会被观察', () => {
    startPhiEdgePhase()
    const mutation = FakeMutationObserver.instances[0]
    expect(mutation).toBeDefined()

    const late = mountCard()
    mutation.trigger()
    vi.advanceTimersByTime(120)

    expect(late.getAttribute('data-phi-edge-phase')).toBe('')
    expect(FakeIntersectionObserver.instances[0].targets.has(late)).toBe(true)
  })

  it('stop() 断开所有观察器', () => {
    mountCard()
    startPhiEdgePhase()
    const io = FakeIntersectionObserver.instances[0]
    const mutation = FakeMutationObserver.instances[0]

    stopPhiEdgePhase()

    expect(io.disconnected).toBe(true)
    expect(io.targets.size).toBe(0)
    expect(mutation.disconnected).toBe(true)
  })

  it('低 GPU 档完全不启动（不建观察器、不写属性）', () => {
    document.documentElement.setAttribute('data-low-gpu', '')
    const card = mountCard()

    startPhiEdgePhase()

    expect(FakeIntersectionObserver.instances).toHaveLength(0)
    expect(FakeMutationObserver.instances).toHaveLength(0)
    expect(card.hasAttribute('data-phi-edge-phase')).toBe(false)
    expect(card.style.getPropertyValue('--phi-edge-delay')).toBe('')
  })

  it('缺少 IntersectionObserver 时只做相位，不抛错', () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    const card = mountCard()

    expect(() => startPhiEdgePhase()).not.toThrow()
    expect(card.getAttribute('data-phi-edge-phase')).toBe('')
    expect(card.hasAttribute('data-phi-edge-idle')).toBe(false)
  })
})

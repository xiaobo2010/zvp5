import { afterEach, describe, expect, it, vi } from 'vitest'
import { abortAllAiRuns, activeAiRunCount, registerAiAbort } from './activeRuns'

/**
 * 全局中止登记表：注册/注销、幂等、不让单个回调的异常扩散。
 */

const disposers: Array<() => void> = []

function register(handle: (reason?: string) => void): () => void {
  const off = registerAiAbort(handle)
  disposers.push(off)
  return off
}

afterEach(() => {
  for (const off of disposers.splice(0)) {
    off()
  }
})

describe('AI 在途请求登记表', () => {
  it('注册后能被统一中止，注销后不再收到信号', () => {
    const seen: Array<string | undefined> = []
    const off = register((reason) => seen.push(reason))

    abortAllAiRuns('banned')
    expect(seen).toEqual(['banned'])

    off()
    abortAllAiRuns('banned')
    expect(seen).toEqual(['banned'])
  })

  it('注销函数可以重复调用', () => {
    const handle = vi.fn()
    const off = register(handle)
    off()
    expect(() => off()).not.toThrow()
    abortAllAiRuns()
    expect(handle).not.toHaveBeenCalled()
  })

  it('计数反映当前登记数', () => {
    const before = activeAiRunCount()
    const off = register(() => {})
    expect(activeAiRunCount()).toBe(before + 1)
    off()
    expect(activeAiRunCount()).toBe(before)
  })

  it('单个回调抛错不影响其它请求，也不向调用方冒泡', () => {
    const healthy = vi.fn()
    register(() => {
      throw new Error('boom')
    })
    register(healthy)

    expect(() => abortAllAiRuns('banned')).not.toThrow()
    expect(healthy).toHaveBeenCalledWith('banned')
  })

  it('重入不会无限递归', () => {
    let calls = 0
    register(() => {
      calls += 1
      abortAllAiRuns('again')
    })

    expect(() => abortAllAiRuns('banned')).not.toThrow()
    expect(calls).toBe(1)
  })
})

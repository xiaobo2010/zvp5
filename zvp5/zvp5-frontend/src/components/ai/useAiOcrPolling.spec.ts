import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAiOcrPolling } from './useAiOcrPolling'
import type { OcrResult } from '@/../types'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('OCR 轮询', () => {
  it('跟随状态机，直到 done 才停下', async () => {
    const results: OcrResult[] = [
      { status: 'pending' },
      { status: 'processing' },
      { status: 'done', text: '识别结果', engine: 'tencent', confidence: 0.92 }
    ]
    const fetchResult = vi.fn(async () => results.shift())
    const onUpdate = vi.fn()
    const polling = useAiOcrPolling({ intervalMs: 100, fetchResult, onUpdate })

    polling.start('oid-1')
    await vi.advanceTimersByTimeAsync(0)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate.mock.calls[0]![1]).toMatchObject({ status: 'pending' })

    await vi.advanceTimersByTimeAsync(100)
    expect(onUpdate).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(100)
    expect(onUpdate).toHaveBeenCalledTimes(3)
    expect(onUpdate.mock.calls[2]![1]).toMatchObject({ status: 'done' })

    // done 之后不再发请求
    await vi.advanceTimersByTimeAsync(1000)
    expect(onUpdate).toHaveBeenCalledTimes(3)
    expect(fetchResult).toHaveBeenCalledTimes(3)
    expect(polling.isRunning('oid-1')).toBe(false)
  })

  it('识别失败同样停下', async () => {
    const fetchResult = vi.fn(async (): Promise<OcrResult | undefined> => ({
      status: 'failed'
    }))
    const onUpdate = vi.fn()
    const polling = useAiOcrPolling({ intervalMs: 100, fetchResult, onUpdate })

    polling.start('oid-2')
    await vi.advanceTimersByTimeAsync(0)
    expect(onUpdate).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchResult).toHaveBeenCalledTimes(1)
    expect(polling.isRunning('oid-2')).toBe(false)
  })

  it('连续失败到上限就终止并回调', async () => {
    const failure = new Error('network down')
    const fetchResult = vi.fn(async () => {
      throw failure
    })
    const onError = vi.fn()
    const onUpdate = vi.fn()
    const polling = useAiOcrPolling({
      intervalMs: 100,
      maxErrors: 3,
      fetchResult,
      onUpdate,
      onError
    })

    polling.start('oid-3')
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(100)
    await vi.advanceTimersByTimeAsync(100)

    expect(onUpdate).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0]![0]).toBe('oid-3')
    expect(onError.mock.calls[0]![1]).toBe(failure)
    expect(polling.isRunning('oid-3')).toBe(false)

    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchResult).toHaveBeenCalledTimes(3)
  })

  it('stop 之后不再请求', async () => {
    const fetchResult = vi.fn(async (): Promise<OcrResult | undefined> => ({
      status: 'processing'
    }))
    const polling = useAiOcrPolling({ intervalMs: 100, fetchResult })

    polling.start('oid-4')
    await vi.advanceTimersByTimeAsync(0)
    polling.stop('oid-4')
    await vi.advanceTimersByTimeAsync(1000)

    expect(fetchResult).toHaveBeenCalledTimes(1)
    expect(polling.isRunning('oid-4')).toBe(false)
  })

  it('stopAll 清掉全部在跑的轮询', async () => {
    const fetchResult = vi.fn(async (): Promise<OcrResult | undefined> => ({
      status: 'processing'
    }))
    const polling = useAiOcrPolling({ intervalMs: 100, fetchResult })

    polling.start('oid-5')
    polling.start('oid-6')
    await vi.advanceTimersByTimeAsync(0)
    polling.stopAll()
    await vi.advanceTimersByTimeAsync(1000)

    expect(fetchResult).toHaveBeenCalledTimes(2)
    expect(polling.running.value).toEqual([])
  })
})

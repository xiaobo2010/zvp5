import { getCurrentInstance, onUnmounted, ref } from 'vue'
import { getOcr } from '@/api/attachment/manage'
import type { OcrResult, OcrStatus } from '@/../types'

/**
 * OCR 结果轮询。
 *
 * 网关受理（POST /ai/ocr）只把活丢进后台队列，结果要靠轮询
 * GET /attachments/{oid}/ocr 取。轮询的三个硬要求：
 *
 * 1. 状态机只在 done 与 failed 停下，pending 与 processing 继续查；
 * 2. 连续失败若干次后终止，不让一个坏附件把定时器一直挂着；
 * 3. 组件卸载时必须停表，否则卸载后还在发请求。
 */

export interface AiOcrPollingOptions {
  /** 轮询间隔，默认 2500 毫秒。 */
  intervalMs?: number
  /** 连续失败多少次后终止，默认 3 次。 */
  maxErrors?: number
  /** 取一次结果的实现，默认走附件接口。 */
  fetchResult?: (oid: string) => Promise<OcrResult | undefined>
  /** 状态更新回调。 */
  onUpdate?: (oid: string, result: OcrResult) => void
  /** 连续失败终止时的回调。 */
  onError?: (oid: string, error: unknown) => void
}

const DONE_STATUSES: OcrStatus[] = ['done', 'failed']

export function useAiOcrPolling(options: AiOcrPollingOptions = {}) {
  const intervalMs = options.intervalMs ?? 2500
  const maxErrors = options.maxErrors ?? 3
  const fetchResult = options.fetchResult ?? getOcr

  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const failures = new Map<string, number>()
  // poll 在飞期间没有 timer，只靠 timers 判重会让重复 start 起两轮轮询
  const inFlight = new Set<string>()
  const running = ref<string[]>([])

  function clearTimer(oid: string) {
    const timer = timers.get(oid)
    if (timer !== undefined) {
      clearTimeout(timer)
      timers.delete(oid)
    }
  }

  function stop(oid: string) {
    clearTimer(oid)
    failures.delete(oid)
    running.value = running.value.filter((item) => item !== oid)
  }

  function stopAll() {
    for (const oid of Array.from(timers.keys())) {
      clearTimer(oid)
    }
    timers.clear()
    failures.clear()
    running.value = []
  }

  function schedule(oid: string) {
    clearTimer(oid)
    timers.set(
      oid,
      setTimeout(() => {
        void poll(oid)
      }, intervalMs)
    )
  }

  function scheduleFailure(oid: string, error?: unknown) {
    const count = (failures.get(oid) ?? 0) + 1
    failures.set(oid, count)
    if (count >= maxErrors) {
      stop(oid)
      options.onError?.(oid, error)
      return
    }
    schedule(oid)
  }

  async function poll(oid: string) {
    try {
      const result = await fetchResult(oid)
      failures.delete(oid)
      if (!result) {
        scheduleFailure(oid)
        return
      }
      options.onUpdate?.(oid, result)
      if (DONE_STATUSES.includes(result.status)) {
        stop(oid)
        return
      }
      schedule(oid)
    } catch (error) {
      scheduleFailure(oid, error)
    } finally {
      inFlight.delete(oid)
    }
  }

  /** 开始跟踪一个附件的识别进度；已在跟踪（含 poll 在飞）时忽略。 */
  function start(oid: string) {
    if (!oid || timers.has(oid) || inFlight.has(oid)) {
      return
    }
    inFlight.add(oid)
    running.value = [...running.value, oid]
    void poll(oid)
  }

  function isRunning(oid: string): boolean {
    return timers.has(oid)
  }

  if (getCurrentInstance()) {
    onUnmounted(stopAll)
  }

  return { start, stop, stopAll, isRunning, running }
}

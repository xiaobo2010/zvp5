import type { InternalAxiosRequestConfig } from 'axios'

export type TrackedRequestConfig = InternalAxiosRequestConfig & {
  __inflightKey?: symbol
  __retried?: boolean
}

const inFlight = new Map<symbol, AbortController>()

/**
 * @description Attach a tracked AbortController to a request config so that the
 * request can be cancelled as a group (e.g. when the route changes). An already
 * supplied `signal` is chained into the tracked controller.
 */
export function trackRequest(config: InternalAxiosRequestConfig): void {
  const controller = new AbortController()
  const external = config.signal
  if (external) {
    if (external.aborted) {
      controller.abort()
    } else {
      external.addEventListener?.('abort', () => controller.abort())
    }
  }
  const key = Symbol('zvms-inflight')
  ;(config as TrackedRequestConfig).__inflightKey = key
  inFlight.set(key, controller)
  config.signal = controller.signal
}

/**
 * @description Drop the tracked controller associated with a settled request.
 */
export function releaseRequest(config?: InternalAxiosRequestConfig): void {
  const key = (config as TrackedRequestConfig | undefined)?.__inflightKey
  if (key) {
    inFlight.delete(key)
  }
}

/**
 * @description Abort every request that is currently in flight.
 */
export function cancelInFlightRequests(): void {
  for (const controller of inFlight.values()) {
    controller.abort()
  }
  inFlight.clear()
}

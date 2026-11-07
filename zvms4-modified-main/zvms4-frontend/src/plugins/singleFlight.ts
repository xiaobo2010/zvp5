/**
 * 同键并发请求合并（single-flight）。
 *
 * 为什么要合并：浏览器对「跨域 + 非安全请求头」的请求必须先发一次 OPTIONS 预检，
 * 而预检缓存键包含**完整 URL（含 query）**与**请求头集合**。冷缓存下并发发出 N 个
 * 相同请求，浏览器会发 N 次预检（不做在途合并），每次预检又是一轮完整往返。
 * 实测同一秒内 `/api/ai/conversations/{id}` 会被预检 5 次、真实请求 4 次。
 *
 * 这里把「同一时刻、同一身份、同一 URL 的 GET」合并成一次网络往返：预检次数与
 * 真实请求次数一起降为 1。
 *
 * 边界（刻意收窄，避免改变既有语义）：
 * - 只合并**在途**请求，不做任何结果缓存——响应的新鲜度仍由发起方决定；
 * - 只合并 GET：写请求不可共享；
 * - 调用方自带 signal / 进度回调 / 非 JSON 文本响应的一律不参与；
 * - 跟随者拿到的是深拷贝，不会与发起者互相污染数据。
 */

export interface FlightHandle<T> {
  /** 本次请求是否是新航班的发起者（发起者负责真正发出网络请求）。 */
  leader: boolean
  /** 航班的最终结果；发起者失败时同样 reject。 */
  promise: Promise<T>
  /** 发起者：把结果广播给所有跟随者，并结束这个航班。 */
  settle: (value: T) => void
  /** 发起者：终态失败时广播错误，并结束这个航班。 */
  fail: (error: unknown) => void
}

interface Pending<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (error: unknown) => void
}

const flights = new Map<string, Pending<unknown>>()

/**
 * 加入（或开启）一个航班。
 *
 * 同一个 key 在途时返回同一个 promise；首个调用者是发起者，负责在拿到最终结果后
 * 调用 `settle` / `fail` 广播出去。
 */
export function joinFlight<T>(key: string): FlightHandle<T> {
  const existing = flights.get(key) as Pending<T> | undefined
  if (existing) {
    return {
      leader: false,
      promise: existing.promise,
      settle: () => {},
      fail: () => {}
    }
  }

  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  // 航班可能没有任何跟随者，发起者失败时这个 promise 会无人认领。
  // 挂一个空处理器把「未处理的 rejection」消掉，跟随者拿到的仍是同一个 rejection。
  promise.catch(() => {})

  const pending: Pending<T> = { promise, resolve, reject }
  flights.set(key, pending as Pending<unknown>)

  const end = (action: () => void) => {
    if (flights.get(key) !== (pending as Pending<unknown>)) {
      return
    }
    flights.delete(key)
    action()
  }

  return {
    leader: true,
    promise,
    settle: (value) => end(() => resolve(value)),
    fail: (error) => end(() => reject(error))
  }
}

/** 当前在途航班数（测试用）。 */
export function pendingFlightCount(): number {
  return flights.size
}

/** 清空在途航班（测试隔离用）。 */
export function clearFlights(): void {
  flights.clear()
}

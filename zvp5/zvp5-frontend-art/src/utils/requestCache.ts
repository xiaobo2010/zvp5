/**
 * 进程内 GET 响应缓存。
 *
 * 目的是消除「每次进入页面都重新请求」造成的卡顿：同一会话内再次打开列表、
 * 成员、版本号等只读接口时，直接返回上一次的结果，不再走网络。
 *
 * - 有效期很短（默认 30 秒），写操作成功后会立刻整体失效，保证数据不会明显过期；
 * - 版本号这类几乎不变的接口用更长的有效期；
 * - 命中缓存时给调用方一份深拷贝，避免页面改动数据时污染缓存。
 */
const MAX_ENTRIES = 120
const DEFAULT_TTL = 30 * 1000
const VERSION_TTL = 10 * 60 * 1000

export interface MemoryEntry {
  t: number
  data: unknown
}

const entries = new Map<string, MemoryEntry>()

export function ttlFor(key: string): number {
  return key.toLowerCase().includes('/version') ? VERSION_TTL : DEFAULT_TTL
}

export function memoryGet(key: string): MemoryEntry | undefined {
  return entries.get(key)
}

export function memorySet(key: string, data: unknown): void {
  entries.set(key, { t: Date.now(), data })
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.keys().next().value
    if (oldest === undefined) {
      break
    }
    entries.delete(oldest)
  }
}

export function clearMemoryCache(): void {
  entries.clear()
}

/** 缓存命中时复制一份，调用方后续修改不会影响缓存内容 */
export function cloneForCaller<T>(value: T): T {
  try {
    return structuredClone(value)
  } catch {
    return value
  }
}

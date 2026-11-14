/**
 * 在途 AI 请求的中止登记处。
 *
 * 为什么要有这一层：封禁信号（403 `USER_BANNED`）到达时，已经发出去的流式
 * 连接不会自己停——后端即便不再产内容，连接还在、字还在往界面上吐。封禁是
 * 「全局失效」，不是某一次请求的局部失败，所以中断入口必须与请求发起点解耦：
 * 发起点把「怎么中止自己」注册进来，封禁信号只需要喊一声。
 *
 * 契约：
 * - 注册返回注销函数，正常收尾（finally）里必须调用，登记表才能保持干净；
 * - `abortAllAiRuns` 幂等、不抛错：单个中止回调炸了不能拖累其它请求；
 * - 回调里不得再调 `abortAllAiRuns`（重入直接忽略，避免无限递归）。
 */

type AiAbortHandler = (reason?: string) => void

const handlers = new Set<AiAbortHandler>()

/** 正在分发中止信号的标志：重入直接放弃，保证幂等且不会自递归。 */
let dispatching = false

/**
 * 注册一个中止回调，返回注销函数（幂等，可重复调用）。
 *
 * 回调签名兼容无参函数：`registerAiAbort(() => controller.abort())`。
 * `reason` 由中止发起方给出（如 `'banned'`），回调可选使用。
 */
export function registerAiAbort(handle: AiAbortHandler): () => void {
  if (typeof handle !== 'function') {
    return () => {}
  }
  handlers.add(handle)
  let released = false
  return () => {
    if (released) {
      return
    }
    released = true
    handlers.delete(handle)
  }
}

/**
 * 中止所有在途 AI 请求（流式连接、轮询等）。
 *
 * 幂等、不抛错；不负责注销登记——每个发起点在自己的 finally 里注销，
 * 这样「中止后没走完收尾」的异常路径也能被下一次中止重试。
 */
export function abortAllAiRuns(reason?: string): void {
  if (dispatching) {
    return
  }
  dispatching = true
  try {
    // 快照：回调里注销自己（改 Set）不影响本次分发。
    for (const handle of Array.from(handlers)) {
      try {
        handle(reason)
      } catch {
        // 单个请求中止失败不能挡住其它请求。
      }
    }
  } finally {
    dispatching = false
  }
}

/** 当前登记在案的在途中止点数量（测试/调试用）。 */
export function activeAiRunCount(): number {
  return handlers.size
}

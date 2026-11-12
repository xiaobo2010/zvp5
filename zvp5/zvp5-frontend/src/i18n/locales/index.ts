import * as activity from './activity'
import * as ai from './ai'
import * as manage from './manage'
import * as server from './server'
import { coreZhCN } from './core'

export { coreZhCN } from './core'
export * from './namespaces'

/**
 * 全量字典：仅供测试 / 工具同步取用。
 *
 * 生产入口（main → App → axios…）一律从 `./core` 与 `./namespaces` 取，
 * 不要 import 本文件——这里的静态 import 会把 4 个懒加载字典拉回首屏 chunk。
 * 该约束由构建产物验证：dist 中不应再有四份字典字符串。
 */
export const zhCN = {
  ...coreZhCN,
  activity: activity.zhCN,
  ai: ai.zhCN,
  manage: manage.zhCN,
  server: server.zhCN
}

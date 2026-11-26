/**
 * 大字典的路由懒加载。
 *
 * ai / manage / server / activity 四份消息合计约 124KB 源码，此前随入口静态打进
 * 首屏 chunk。这里改为：路由守卫按路径 await 对应字典，空闲时再补齐其余字典
 * （见 src/i18n/index.ts）。模块缓存天然幂等，不另建消息缓存表。
 */
import { coreZhCN } from './core'

export type LocaleNamespace = 'ai' | 'manage' | 'server' | 'activity'

export const LAZY_NAMESPACES: LocaleNamespace[] = ['ai', 'manage', 'server', 'activity']

export interface NamespaceRule {
  /** 命中任一前缀即视为该规则（按段匹配，不做裸 startsWith）。 */
  prefixes: string[]
  /** 显式排除的前缀，先于 namespaces 判定。 */
  exclude?: string[]
  namespaces: LocaleNamespace[]
}

/**
 * 路径 → 字典。顺序敏感：先精确后宽泛，取第一条命中的规则。
 *
 * 归属来自实际 grep 结果，不是猜的：
 *   * /manage 下的 ManageRoles 复用 ZSystemAuthDialog，对话框文案在 server.auth.*；
 *   * /group 下是 ZGroupUserList / ZActivityList（manage + activity）；
 *   * /user/:id 的学生详情页直接用了 manage.* 与 activity.*；
 *   * /user 首页的今日工作表与时长面板用了 activity.status / activity.mode。
 *
 * ai 字典的归属同理，但更容易漏：这批页面本身不在 /ai 下，却各自引用了
 * ai.* 文案——组列表/成员页的「额度」入口（ai.userQuota.*）、成员额度对话框
 * （ai.quotaAdmin.*）、义工活动页的 OCR（ai.ocr.*）、选人框（ai.select.*）、
 * Markdown 编辑器的 AI 对话框（ai.chat.thinkingSection）。这些页面若不加载
 * ai 字典，界面会直接显示原始 key（如 ai.quotaAdmin.afTitle）。
 */
export const NAMESPACE_RULES: NamespaceRule[] = [
  { prefixes: ['/ai'], namespaces: ['ai'] },
  { prefixes: ['/server'], namespaces: ['server'] },
  { prefixes: ['/manage'], namespaces: ['manage', 'server', 'ai'] },
  { prefixes: ['/group'], namespaces: ['manage', 'activity', 'ai'] },
  { prefixes: ['/activity', '/activities'], namespaces: ['activity', 'ai'] },
  { prefixes: ['/user/'], namespaces: ['manage', 'activity', 'ai'], exclude: ['/user/login'] },
  { prefixes: ['/user'], namespaces: ['activity', 'ai'], exclude: ['/user/login'] }
]

/** 只吃首屏字典的路径：根、登录、关于、个人资料、维护页与 404 兜底。 */
export const CORE_EXACT_PATHS = ['/', '/:pathMatch(.*)*']
/**
 * /notifications 走 notificationCenter + nav：两者都在首屏字典里（core.ts），
 * 因此和 /me 一样归入核心路径，无需按路由懒加载任何大字典。
 */
export const CORE_PREFIX_PATHS = [
  '/about',
  '/me',
  '/notifications',
  '/user/login',
  '/maintenance'
]

function underPath(path: string, prefix: string): boolean {
  if (path === prefix) return true
  return path.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`)
}

export function namespacesForPath(path: string): LocaleNamespace[] {
  for (const rule of NAMESPACE_RULES) {
    if (rule.exclude?.some((prefix) => underPath(path, prefix))) continue
    if (rule.prefixes.some((prefix) => underPath(path, prefix))) {
      return rule.namespaces
    }
  }
  return []
}

export function isCorePath(path: string): boolean {
  if (CORE_EXACT_PATHS.includes(path)) return true
  return CORE_PREFIX_PATHS.some((prefix) => underPath(path, prefix))
}

const NAMESPACE_IMPORTERS: Record<LocaleNamespace, () => Promise<Record<string, unknown>>> = {
  ai: async () => ({ ai: (await import('./ai')).zhCN }),
  manage: async () => ({ manage: (await import('./manage')).zhCN }),
  server: async () => ({ server: (await import('./server')).zhCN }),
  activity: async () => ({ activity: (await import('./activity')).zhCN })
}

// 已装载字典的运行时视图：vue-i18n 实例由 i18n/index.ts 合并，这里保留一份给
// errorMessage 的 translateKey 做同步取值（api 层错误文案要能立刻解析 key）。
let runtimeMessages: Record<string, unknown> = { ...coreZhCN }
const loadedNamespaces = new Set<LocaleNamespace>()

export function localeMessages(): Record<string, unknown> {
  return runtimeMessages
}

export function isNamespaceLoaded(name: LocaleNamespace): boolean {
  return loadedNamespaces.has(name)
}

/** 拉取单个字典；重复调用只命中模块缓存，返回值形如 `{ ai: {...} }`。 */
export async function loadNamespace(name: LocaleNamespace): Promise<Record<string, unknown>> {
  if (loadedNamespaces.has(name)) {
    return { [name]: runtimeMessages[name] }
  }
  const messages = await NAMESPACE_IMPORTERS[name]()
  runtimeMessages = { ...runtimeMessages, ...messages }
  loadedNamespaces.add(name)
  return messages
}

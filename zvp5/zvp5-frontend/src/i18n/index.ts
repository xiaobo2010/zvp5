import { createI18n } from 'vue-i18n'
import { coreZhCN } from './locales/core'
import {
  LAZY_NAMESPACES,
  loadNamespace,
  namespacesForPath,
  type LocaleNamespace
} from './locales/namespaces'

const i18n = createI18n({
  locale: 'zh-CN',
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': coreZhCN
  },
  legacy: false
})

// 已合并进 vue-i18n 的字典：避免每次导航都 setLocaleMessage 重建整份消息表
// （那会让所有 t() 依赖重新求值）。模块缓存仍是唯一的下载/解析层。
const appliedNamespaces = new Set<LocaleNamespace>()

/**
 * 按路径补齐该路由需要的字典。
 *
 * 单个字典加载失败只让对应文案退化成 key，不能把导航（尤其维护模式跳转）
 * 一起拖死，所以逐个 catch。
 */
export async function ensureLocaleForPath(path: string): Promise<void> {
  const wanted = namespacesForPath(path).filter((name) => !appliedNamespaces.has(name))
  if (wanted.length === 0) {
    return
  }
  const results = await Promise.all(
    wanted.map(async (name) => {
      try {
        return { name, messages: await loadNamespace(name) }
      } catch {
        return null
      }
    })
  )
  const incoming: Record<string, unknown> = {}
  const merged: LocaleNamespace[] = []
  for (const result of results) {
    if (!result) {
      continue
    }
    Object.assign(incoming, result.messages)
    merged.push(result.name)
  }
  if (merged.length === 0) {
    return
  }
  const global = i18n.global
  global.setLocaleMessage('zh-CN', { ...global.getLocaleMessage('zh-CN'), ...incoming })
  for (const name of merged) {
    appliedNamespaces.add(name)
  }
}

/**
 * 空闲时补齐其余字典，让后续点击导航零等待。
 *
 * 省流模式 / 2G 弱网跳过（与 utils/routePrefetch.ts 的阈值保持一致）：
 * 真到那个网络条件，首屏带宽比"导航快一点"重要。
 */
export function prefetchRemainingLocales(): void {
  const connection = (
    navigator as Navigator & {
      connection?: { saveData?: boolean; effectiveType?: string }
    }
  ).connection
  if (connection?.saveData || /^2g$/.test(connection?.effectiveType ?? '')) {
    return
  }
  const warm = () => {
    for (const name of LAZY_NAMESPACES) {
      loadNamespace(name).catch(() => {})
    }
  }
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(warm, { timeout: 5000 })
  } else {
    window.setTimeout(warm, 1200)
  }
}

export default i18n

/**
 * 积极预取：把「点击导航 → 下载 chunk → 渲染」的断裂感提前抹平。
 *
 * 背景：路由组件全部是 `() => import()` 懒加载（router/index.ts），此前
 * 无任何预取——平板（三星 P615C/P620）上点击导航项才开始下载 chunk，
 * 等待期间路由切换动画先走完再白屏一下，是导航断裂感的直接来源。
 *
 * 策略：
 *   * 登录后导航挂载时（UserNav）watch 可见导航项，requestIdleCallback
 *     空闲时批量预取——`import()` 调用即下载并执行模块（组件对象创建
 *     成本极低，不挂载），浏览器与打包器模块缓存保证真实导航零重复。
 *   * 指针悬停/触摸按下（pointerenter）时立即预取目标路由——tap 到
 *     click 的百毫秒窗口足够完成本地缓存的 chunk 命中。
 *   * 重复预取靠 Set 短路；一切错误静默吞掉（预取失败不应影响正常导航）。
 *
 * 另含 phi 字体预热（warmupFonts）：Bebas/Rajdhani 是 phi 主题的显示
 * 字体（font-display: swap），首次进入 phi 页面前的空闲预热可消掉
 * 登录揭示动画与首页大标题的字体跳动（FOUT）。
 */

import type { Router } from 'vue-router'

const prefetchedPaths = new Set<string>()

export function prefetchRoute(router: Router, path: string): void {
  if (prefetchedPaths.has(path)) return
  prefetchedPaths.add(path)
  let matched
  try {
    matched = router.resolve(path).matched
  } catch {
    return
  }
  for (const record of matched) {
    const component = record.components?.default
    if (typeof component === 'function') {
      Promise.resolve((component as () => unknown)()).catch(() => {})
    }
  }
}

export function prefetchRoutesWhenIdle(router: Router, paths: string[], timeoutMs = 2000): void {
  const warm = () => {
    for (const path of paths) prefetchRoute(router, path)
  }
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(warm, { timeout: timeoutMs })
  } else {
    window.setTimeout(warm, 300)
  }
}

export function warmupFonts(specs: string[]): void {
  if (typeof document === 'undefined' || !document.fonts) return
  for (const spec of specs) {
    document.fonts.load(spec, '0123456789').catch(() => {})
  }
}

export function warmupFontsWhenIdle(specs: string[], timeoutMs = 4000): void {
  const warm = () => warmupFonts(specs)
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(warm, { timeout: timeoutMs })
  } else {
    window.setTimeout(warm, 1200)
  }
}

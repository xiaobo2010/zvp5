import { createHash } from 'node:crypto'
import type { Plugin } from 'vite'

/**
 * 构建期把 index.html 里的全部内联脚本搬进外置文件（CSP 整改，2026-10-23）。
 *
 * 背景：`@vitejs/plugin-legacy` 会往产物 index.html 注入 4 段内联引导脚本
 * （现代浏览器探测 / legacy 兜底加载器 / Safari 10.1 nomodule 修复 / legacy
 * 入口引导），生产 nginx 的 CSP 因此被迫在 `script-src` 里挂 `'unsafe-inline'`。
 * Vite 不提供「内联脚本外置」选项；改用 sha256 哈希白名单又要随构建/插件升级
 * 同步维护——一旦失配，现代端探测脚本被 CSP 拦掉，`window.__vite_is_modern_browser`
 * 未定义，legacy 兜底加载器跟着抢跑，**全站白屏**。
 *
 * 所以在 closeBundle 阶段（rolldown-vite 在 writeBundle 时尚未把 index.html
 * 落盘）直接搬移：内联脚本变成外置文件后可由 `script-src` 按资源域放行，产物里
 * 不允许再出现任何内联脚本（出现即构建失败）。搬移不改变执行语义：
 *   * `type="module"` 的内联与外置同为 in-order module script，按文档顺序执行；
 *   * `nomodule` 的经典脚本外置后加载/拦截行为不变；
 *   * plugin-legacy 的脚本只引用元素 id / `data-src`，与自身是否内联无关。
 *
 * 插件必须在 vite.config.ts 里注册在 vite-plugin-pwa **之前**：closeBundle 按
 * 数组顺序执行，先搬移再生成 SW，precache 才能收录外置文件；而 legacy 的
 * index.html 注入发生在 generateBundle 阶段，天然早于 closeBundle。本钩子幂等：
 * 重复运行时无内联脚本体，直接跳过。
 */
export function externalizeInlineScripts(): Plugin {
  let assetBase = '/assets/'
  return {
    name: 'zvms-externalize-inline-scripts',
    apply: 'build',
    configResolved(config) {
      assetBase = `${config.base.replace(/\/+$/, '')}/assets/`
    },
    /**
     * Vite 8（内置 rolldown）不再把 index.html 放进 rolldown bundle，落盘时机
     * 也晚于 closeBundle，旧实现读 dist/index.html 会 ENOENT。改为在
     * transformIndexHtml 的 post 阶段改写最终 HTML 并 emit 外置脚本：
     * plugin-legacy 的注入同样是 post 且注册在前，因此这里能拿到含内联引导
     * 脚本的最终 HTML，emit 的资源会随正常产物一起落盘。
     */
    transformIndexHtml: {
      order: 'post',
      handler(html) {
        const scriptTag = /<script\b([^>]*?)>([\s\S]*?)<\/script>/gi
        const pending: Array<{ filename: string; body: string }> = []
        const seen = new Set<string>()

        let next = html.replace(scriptTag, (_match, attrsRaw: string, body: string) => {
          // 只跳过真正的外置脚本：`(?:^|\s)src=` 不含 `data-src`（legacy-entry
          // 标签带 data-src 且仍有内联体，误跳会让平板 legacy 路径被 CSP 拦死）。
          if (/(?:^|\s)src\s*=/i.test(attrsRaw)) return _match
          if (!body.trim()) return _match
          const hash = createHash('sha256').update(body).digest('hex').slice(0, 8)
          const filename = `boot-${hash}.js`
          if (!seen.has(filename)) {
            seen.add(filename)
            pending.push({ filename, body })
          }
          return `<script${attrsRaw} src="${assetBase}${filename}"></script>`
        })

        for (const { filename, body } of pending) {
          this.emitFile({ type: 'asset', fileName: `assets/${filename}`, source: body })
        }

        // 复扫：仍存在内联脚本体即构建失败，杜绝 CSP 静默回归 'unsafe-inline'。
        const leftovers: string[] = []
        for (const m of next.matchAll(scriptTag)) {
          if (!/(?:^|\s)src\s*=/i.test(m[1]) && m[2].trim()) {
            leftovers.push(m[0].slice(0, 80))
          }
        }
        if (leftovers.length > 0) {
          this.error(
            `[externalize-inline-scripts] index.html 仍存在未外置的内联脚本：\n${leftovers.join('\n')}`
          )
        }

        if (pending.length > 0) {
          console.log(
            `[externalize-inline-scripts] 已外置 ${pending.length} 段内联脚本 → ${pending
              .map((p) => p.filename)
              .join(', ')}`
          )
        }
        return next
      }
    }
  }
}

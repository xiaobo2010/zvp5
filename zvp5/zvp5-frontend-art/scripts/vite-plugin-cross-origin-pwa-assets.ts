import { promises as fs } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

function staticAssetPath(base: string, relativePath: string): string {
  return base === '/' ? `/${relativePath}` : `${base}${relativePath}`
}

/**
 * Move the generated manifest link and all Workbox precache entries to the
 * configured static origin. The Service Worker registration itself must stay
 * on the page origin, so only this post-processing is cross-origin.
 *
 * vite 8 (rolldown) 下插件顺序变了：本插件即便是 transformIndexHtml 的 post
 * 钩子，也会早于 vite-plugin-pwa 注入 manifest link 执行，替换会静默落空。
 * 因此在 generateBundle 里改 emit 出来的 index.html 资源（此时所有
 * transformIndexHtml 都已跑完），sw.js 仍由 pwa 在 closeBundle 落盘，继续用
 * fs 改写。
 */
export function crossOriginPwaAssets(staticBase: string): Plugin {
  let outDir = 'dist'
  return {
    name: 'zvms-cross-origin-pwa-assets',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
    },
    generateBundle(_options, bundle) {
      if (staticBase === '/') return
      for (const asset of Object.values(bundle)) {
        if (asset.type !== 'asset' || !asset.fileName.endsWith('index.html')) continue
        const source = String(asset.source)
        if (!source.includes('href="/manifest.webmanifest"')) continue
        asset.source = source.replace(
          'href="/manifest.webmanifest"',
          `href="${staticAssetPath(staticBase, 'manifest.webmanifest')}"`
        )
      }
    },
    closeBundle: {
      order: 'post',
      async handler() {
        if (staticBase === '/') return

        const swPath = path.join(outDir, 'sw.js')
        const sw = await fs.readFile(swPath, 'utf8')
        const rewritten = sw.replace(
          /url:"((?!https?:|\/\/|\/)[^"]+)"/g,
          (_match, relativePath: string) => `url:"${staticAssetPath(staticBase, relativePath)}"`
        )
        await fs.writeFile(swPath, rewritten)
      }
    }
  }
}

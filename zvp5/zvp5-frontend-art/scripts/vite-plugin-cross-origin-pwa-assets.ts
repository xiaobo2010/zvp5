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
 */
export function crossOriginPwaAssets(staticBase: string): Plugin {
  let outDir = 'dist'
  return {
    name: 'zvms-cross-origin-pwa-assets',
    apply: 'build',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
    },
    closeBundle: {
      order: 'post',
      async handler() {
        if (staticBase === '/') return

        const htmlPath = path.join(outDir, 'index.html')
        const html = await fs.readFile(htmlPath, 'utf8')
        await fs.writeFile(
          htmlPath,
          html.replace(
            'href="/manifest.webmanifest"',
            `href="${staticAssetPath(staticBase, 'manifest.webmanifest')}"`
          )
        )

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

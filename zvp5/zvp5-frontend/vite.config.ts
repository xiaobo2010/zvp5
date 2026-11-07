import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
// import basicSsl from '@vitejs/plugin-basic-ssl'
import UnoCSS from 'unocss/vite'
import legacy from '@vitejs/plugin-legacy'
import vueDevtools from 'vite-plugin-vue-devtools'
import VueComponents from 'unplugin-vue-components/vite'
import AutoImport from 'unplugin-auto-import/vite'
import Icons from 'unplugin-icons/vite'
import IconsResolver from 'unplugin-icons/resolver'
import { VitePWA as pwa } from 'vite-plugin-pwa'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { externalizeInlineScripts } from './scripts/vite-plugin-externalize-inline-scripts'
import {
  validateOptionalContentApiBase,
  validateOptionalProductionRaceApiBase,
  validateProductionApiBase
} from './src/utils/apiBaseConfig'

function requireProductionApiBase(mode: string): string {
  const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
  return validateProductionApiBase(process.env.VITE_API_BASE || fileEnv.VITE_API_BASE)
}

function optionalProductionRaceApiBase(mode: string): string | undefined {
  const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
  return validateOptionalProductionRaceApiBase(
    process.env.VITE_API_RACE_BASE || fileEnv.VITE_API_RACE_BASE
  )
}

function optionalContentApiBase(mode: string): string | undefined {
  const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
  return validateOptionalContentApiBase(
    process.env.VITE_API_CONTENT_BASE || fileEnv.VITE_API_CONTENT_BASE
  )
}

/**
 * Warm the cross-origin API connection while the HTML is still being parsed.
 *
 * The SPA is served by the content host and calls the API host directly, so the
 * first API request would otherwise pay a full DNS + TCP + TLS round trip after
 * the JS bundle has already loaded.  `preconnect` starts that handshake in
 * parallel with the bundle download from the other origin; `dns-prefetch` is the
 * fallback for browsers that drop speculative connections.
 *
 * The origin is derived from VITE_API_BASE at build time, so no production
 * domain is stored in the repository and a local build (`/api`) emits nothing.
 */
function preconnectApiOrigin(): Plugin {
  let mode = 'production'
  return {
    name: 'zvms4-preconnect-api-origin',
    apply: 'build',
    configResolved(config) {
      mode = config.mode
    },
    transformIndexHtml(html) {
      const fileEnv = loadEnv(mode, process.cwd(), 'VITE_')
      const bases = [
        process.env.VITE_API_BASE || fileEnv.VITE_API_BASE,
        process.env.VITE_API_RACE_BASE || fileEnv.VITE_API_RACE_BASE,
        // 预览站的内容主机是远端 API 域，值得和主基址一样预热。
        process.env.VITE_API_CONTENT_BASE || fileEnv.VITE_API_CONTENT_BASE
      ]
      const origins = new Set<string>()
      for (const raw of bases) {
        try {
          origins.add(new URL(validateOptionalProductionRaceApiBase(raw) || '').origin)
        } catch {
          // 主基址由 requireProductionApiBase 强校验，非法竞速基址同样在 build 前报错。
        }
      }
      if (origins.size === 0) return html
      return {
        html,
        tags: [...origins].flatMap((origin) => [
          {
            tag: 'link',
            attrs: { rel: 'preconnect', href: origin, crossorigin: '' },
            injectTo: 'head-prepend'
          },
          {
            tag: 'link',
            attrs: { rel: 'dns-prefetch', href: origin },
            injectTo: 'head-prepend'
          }
        ])
      }
    }
  }
}

// https://vitejs.dev/config/
export const viteBaseConfig = {
  plugins: [
    vue(),
    vueJsx(),
    legacy({
      targets: [
        'defaults',
        'not IE 11',
        'chrome 64',
        'firefox 78',
        'safari 12',
        'edge 79',
        'Android >= 8'
      ],
      polyfills: ['es.promise', 'es.symbol', 'es.string.replace-all']
    }),
    // basicSsl(),
    UnoCSS(),
    vueDevtools(),
    // CSP 整改：必须注册在 pwa 之前——closeBundle 按数组顺序执行，先搬移
    // index.html 的内联脚本，workbox precache 才能收录新外置文件。
    externalizeInlineScripts(),
    pwa({
      injectRegister: 'auto',
      registerType: 'autoUpdate',
      immediate: true,
      includeAssets: ['favicon.ico', 'apple-touch-icon.png', 'mask-icon.svg'],
      manifest: {
        name: 'ZVMS 4',
        short_name: 'ZVMS',
        description: 'The Zhenhai High School Volunteer Management System 4',
        theme_color: '#ffffff',
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png'
          }
        ]
      },
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        sourcemap: true,
        // legacy 产物仅供老浏览器降级使用，不必进 precache：
        // SW 首装可少拉 ~2.8MB，modern 浏览器按需回退即可。
        // 注意 workbox GenerateSW 没有 exclude 选项，等价物是 globIgnores。
        // monaco（Z-IDE 编辑器）仅服务端文件管理按需使用，不进 precache：
        // SW 首装可省 ~3.2MB；离线时 Z-IDE 不可用属可接受（管理员在线工具）。
        // 注意 monaco 没有独立的 manualChunk 后，产物文件名跟着入口模块走
        // （editor.api-*.js / editor-*.css / ZIde-*.js），所以除了历史命名外都要按新名字排除。
        // 管理端重 chunk（xlsx 报表、chart.js 图表、Server* 页面）同理：运行时在线拉取即可，
        // 不占首装预缓存。
        globIgnores: [
          '**/node_modules/**/*',
          '**/*-legacy-*.js',
          '**/monaco-*.js',
          '**/monaco-*.css',
          '**/editor.api-*.js',
          '**/editor.worker-*.js',
          '**/editor-*.css',
          '**/ZIde-*.js',
          '**/ZIde-*.css',
          // monaco basic-languages 的语法配置 chunk：没有 editor.api 就是死代码，别进首装预缓存。
          '**/html-*.js',
          '**/css-*.js',
          '**/javascript-*.js',
          '**/typescript-*.js',
          '**/python-*.js',
          '**/yaml-*.js',
          '**/xml-*.js',
          '**/sql-*.js',
          '**/ini-*.js',
          '**/dockerfile-*.js',
          '**/markdown-*.js',
          '**/shell-*.js',
          '**/xlsx-*.js',
          '**/chartTheme-*.js',
          '**/Server*-*.js',
          // 上面排除了 chartTheme（chart.js 本体），那么只依赖它的三个图表消费者
          // chunk 一起排除，避免「预缓存了却离线跑不起来」的条目。
          '**/ZGroupTimeStatistics-*.js',
          '**/ZGroupTimeStatistics-*.css',
          '**/DbPerfTrendChart-*.js',
          '**/ZActivityStatsDialog-*.js',
          '**/ZActivityStatsDialog-*.css'
        ],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024
      },
      onRegisteredSW(swUrl, r) {
        if (!r) return

        let checking = false
        const checkForUpdate = async () => {
          // 正在安装时交给浏览器自己完成,避免中断首装
          if (checking || r.installing) return
          if (typeof navigator !== 'undefined' && 'onLine' in navigator && !navigator.onLine) return
          checking = true
          try {
            const resp = await fetch(swUrl, {
              cache: 'no-store',
              headers: {
                cache: 'no-store',
                'cache-control': 'no-cache'
              }
            })
            if (resp?.status === 200) {
              await r.update()
            }
          } catch {
            // 离线/网络抖动时静默,下次可见或下一个周期再试
          } finally {
            checking = false
          }
        }

        // 主探测点:页面重新可见(切回标签页、手机端从后台恢复)时检查一次。
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState === 'visible') {
            void checkForUpdate()
          }
        })

        // 兜底:标签页长期保持可见时,10 分钟检查一次。
        setInterval(() => {
          void checkForUpdate()
        }, 600000)
      }
    }),
    Icons({}),
    AutoImport({
      // 全量样式已由 main.ts 引入(element-plus/theme-chalk/index.css + 覆盖层),
      // 关闭按需样式注入,避免同一份 CSS 被重复打包,也让 vitest 不必加载 .css 副作用链。
      resolvers: [ElementPlusResolver({ importStyle: false }), IconsResolver()]
    }),
    VueComponents({
      resolvers: [ElementPlusResolver({ importStyle: false }), IconsResolver()]
    }),
    preconnectApiOrigin()
  ],
  resolve: {
    alias: {
      '@/../types': fileURLToPath(new URL('./types', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@zvms/zvms4-types': fileURLToPath(new URL('./types', import.meta.url))
    }
  },
  build: {
    rollupOptions: {
      output: {
        // 只做粗粒度拆分：element-plus 与 vue 全家各自成 chunk，
        // 其余交给 rollup 默认的按入口/动态导入切分，避免过度碎片化。
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
          if (
            /node_modules\/(vue|vue-router|pinia|vue-i18n|@vue\/[^/]+|@vueuse\/[^/]+)\//.test(id)
          ) {
            return 'vue-vendor'
          }
          return
        }
      }
    }
  },
  // 本地后端联调：开发服务器把 /api 代理到本机 uvicorn（默认 baseURL 为 /api）
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Web SSH 的 /api/system/ssh/ws 需要 WebSocket 升级；Vite 默认不转发。
        ws: true
      }
    }
  }
}

export default defineConfig(({ command, mode }) => {
  if (command === 'build') {
    requireProductionApiBase(mode)
    optionalProductionRaceApiBase(mode)
    optionalContentApiBase(mode)
  }
  return viteBaseConfig
})

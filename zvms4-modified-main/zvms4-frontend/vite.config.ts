import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
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
import { ElementPlusResolver, VantResolver } from 'unplugin-vue-components/resolvers'

// https://vitejs.dev/config/
export default defineConfig({
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
      resolvers: [ElementPlusResolver({ importStyle: false }), VantResolver(), IconsResolver()]
    }),
    VueComponents({
      resolvers: [ElementPlusResolver({ importStyle: false }), VantResolver(), IconsResolver()]
    })
  ],
  resolve: {
    alias: {
      '@/../types': fileURLToPath(new URL('./types', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@zvms/zvms4-types': fileURLToPath(new URL('./types', import.meta.url))
    }
  }
})

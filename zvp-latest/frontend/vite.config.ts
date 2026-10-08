import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import legacy from '@vitejs/plugin-legacy'
import { VitePWA as pwa } from 'vite-plugin-pwa'
import { visualizer } from 'rollup-plugin-visualizer'

let i = 0

export default defineConfig({
  build: {
    sourcemap: false,
    rolldownOptions: {
      checks: {
        pluginTimings: false
      }
    }
  },
  oxc: {
    target: 'esnext'
  },
  plugins: [
    vue(),
    legacy({
      targets: [
        'Chrome >= 135',
        'Edge >= 135',
        'Safari >= 16',
        'iOS >= 16',
        'Firefox >= 137',
        'chromeAndroid >= 135',
        'not IE 11'
      ],
      polyfills: false,
      renderModernChunks: false,
    }),
    UnoCSS(),
    pwa({
      injectRegister: 'auto',
      registerType: 'autoUpdate',
      immediate: true,
      includeAssets: ['favicon.ico'],
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
        cleanupOutdatedCaches: false,
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
        navigateFallbackDenylist: [/^\/xh\//, /^\/api\//, /^\/stats\d+\.html$/, /^\/dev$/],
        sourcemap: false,
      },
      onRegisteredSW(swUrl, r) {
        r &&
          setInterval(async () => {
            if (r.installing || !navigator) {
              return
            }

            if ('connection' in navigator && !navigator.onLine) {
              return
            }

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
          }, 36000000)
      }
    }),
    visualizer({
      open: false,
      get filename() {
          return `dist/stats${++i}.html`
      },
      gzipSize: true,
      brotliSize: false
    })
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  }
})

import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * ZVP5 Canary · 全新前端
 *
 * 开发服务器默认把 /api 代理到本机后端（127.0.0.1:8000），
 * 生产环境由 nginx 同源反代；也可用 VITE_API_BASE 指向独立 API 源。
 */
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true
      }
    }
  },
  build: {
    target: 'es2020',
    sourcemap: mode !== 'production',
    chunkSizeWarningLimit: 900
  }
}))

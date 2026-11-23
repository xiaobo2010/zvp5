import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig(({ command }) => ({
  // Production builds are served from the Pro Worker under /3d/, so every
  // asset URL has to be prefixed. Dev keeps the root path for convenience.
  base: command === 'build' ? '/3d/' : '/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5283,
    strictPort: true
  },
  preview: {
    port: 5284
  }
}))

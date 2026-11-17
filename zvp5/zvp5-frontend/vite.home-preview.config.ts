import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// A standalone visual preview with no application plugins, API proxy, or service worker.
export default defineConfig({
  plugins: [vue()],
  optimizeDeps: { entries: ['home-preview.html'] },
  server: {
    allowedHosts: ['albwxy.fun']
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  }
})

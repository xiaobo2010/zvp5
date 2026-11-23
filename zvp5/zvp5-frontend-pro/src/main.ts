import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/base.css'

// 静态托管只有根站回落时，旧 history 深链接改写到当前子应用的 hash 路由。
const base = import.meta.env.BASE_URL
const prefix = base === '/' ? '' : base.replace(/\/$/, '')
const rest = window.location.pathname.startsWith(`${prefix}/`)
  ? window.location.pathname.slice(prefix.length + 1)
  : ''
if (rest && rest !== 'index.html') {
  const url = new URL(window.location.href)
  const query = url.search
  url.pathname = base
  url.search = ''
  url.hash = `#/${rest}${query}${url.hash}`
  window.location.replace(url)
}

createApp(App).use(router).mount('#app')

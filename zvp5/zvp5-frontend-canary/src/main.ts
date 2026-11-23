import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './app/router'
import { i18n } from './shared/i18n'
import { useUiStore } from './shared/stores/ui'
import { useSessionStore } from './shared/stores/session'
import { reveal } from './shared/directives/reveal'
import { AUTH_EXPIRED_EVENT, MAINTENANCE_EVENT } from './shared/http/client'

import './design/tokens.css'
import './design/base.css'
import './design/glass.css'
import './design/motion.css'

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

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(i18n)
app.use(router)
app.directive('reveal', reveal)

const ui = useUiStore(pinia)
ui.applyTheme()

const session = useSessionStore(pinia)
window.addEventListener(AUTH_EXPIRED_EVENT, () => {
  session.reset()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

window.addEventListener(MAINTENANCE_EVENT, () => {
  if (router.currentRoute.value.name !== 'maintenance') {
    void router.replace({ name: 'maintenance' })
  }
})

void session.bootstrap()
app.mount('#app')

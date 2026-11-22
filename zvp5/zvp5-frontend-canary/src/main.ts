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

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(i18n)
app.use(router)
app.directive('reveal', reveal)

const ui = useUiStore(pinia)
ui.applyTheme()

const session = useSessionStore(pinia)
void session.bootstrap()

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

app.mount('#app')

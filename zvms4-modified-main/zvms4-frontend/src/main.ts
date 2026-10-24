import './assets/main.css'
import './assets/styles/element/index.scss'

import { createApp, type Plugin } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import i18n from './i18n'

import ElementPlus from 'element-plus'
// import CKEditor from '@ckeditor/ckeditor5-vue'

import 'element-plus/theme-chalk/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import 'vant/lib/index.css'
import 'animate.css/animate.min.css'
import 'virtual:uno.css'

// 义工平台设计系统（《前端设计准则》v1.0），必须最后加载以覆盖 Element Plus 默认样式
import './assets/styles/design-tokens.css'
import './assets/styles/yg-theme.css'

import persistedstate from 'pinia-plugin-persistedstate'

const app = createApp(App)

app.use(createPinia().use(persistedstate))
app.use(router as unknown as Plugin)
app.use(i18n as unknown as Plugin)
// app.use(CKEditor as unknown as Plugin)

app.use(ElementPlus, {
  size: 'default'
})

app.mount('#app')

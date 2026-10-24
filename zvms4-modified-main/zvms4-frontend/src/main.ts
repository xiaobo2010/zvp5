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

// Swiss Style 全局基线层,必须最后加载以覆盖 Element Plus 默认样式
import './assets/styles/flat.css'

// 新版前端设计准则的 --yg-* 令牌层（只定义变量，不改 Element Plus 结构）
import './assets/styles/yg-design-tokens.css'

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

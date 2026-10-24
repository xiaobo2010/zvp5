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

// 义工平台标准样式库（令牌 → 基础 → 组件），由 design:sync 从「前端约定/css」同步而来
import './assets/styles/yg/main.css'

// Element Plus 令牌适配层：把 EP 变量映射到 --yg-* 令牌
import './assets/styles/element-overrides.css'

// 项目自定义层：EP 组件外观收尾 + 应用外壳
import './assets/styles/flat.css'
import './assets/main.css'

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

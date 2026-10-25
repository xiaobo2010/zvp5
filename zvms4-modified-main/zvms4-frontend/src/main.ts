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
import 'virtual:uno.css'

// 义工平台标准样式库（令牌 → 基础 → 组件），由 design:sync 从「前端约定/css」同步而来
import './assets/styles/yg/main.css'

// Element Plus 令牌适配层：把 EP 变量映射到 --yg-* 令牌
import './assets/styles/element-overrides.css'

// 液态玻璃导航（UserNav）：仅该组件使用的强玻璃材质，令牌取自 yg/design-tokens.css
import './assets/styles/yg-glass-nav.css'

// 项目自定义层：EP 组件外观收尾 + 应用外壳
import './assets/styles/flat.css'
import './assets/main.css'

import persistedstate from 'pinia-plugin-persistedstate'

// 表格展开行进出场动画（捕获期监听，对所有 el-table 生效）
import { installTableExpandMotion } from './plugins/tableExpandMotion'

const app = createApp(App)

app.use(createPinia().use(persistedstate))
app.use(router as unknown as Plugin)
app.use(i18n as unknown as Plugin)
// app.use(CKEditor as unknown as Plugin)

app.use(ElementPlus, {
  size: 'default'
})

installTableExpandMotion()

app.mount('#app')

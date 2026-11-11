import './assets/styles/element/index.scss'

import { createApp, type Plugin } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import i18n from './i18n'

import { ElLoading } from 'element-plus/es/components/loading/index.mjs'
// import CKEditor from '@ckeditor/ckeditor5-vue'

import 'element-plus/theme-chalk/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
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

// 普通用户端 v2（愚人节ui）：导航/页面进入/首页/活动/关于的布局层
import './assets/styles/yg/user-v2.css'

// Phigros 主题（愚人节ui）：暗色 + 青色 + 锐利几何 + 纯装饰判定线/网格
import './assets/styles/yg/phi-theme.css'
// Phi 遗留普通用户页面皮肤：登记 / 创建 / 详情流程的切角面板与表格
import './assets/styles/yg/phi-legacy-pages.css'
// Phi 主题的 body 级弹层（Teleport 逃出 .yg-phi 的 Dialog/Message/Popover）
import './assets/styles/yg/phi-portals.css'

// 平板滚动流畅层：滚动进行中暂停纯装饰动画（性能规则，不承载视觉设计）
import './assets/styles/perf-tablet.css'

import persistedstate from 'pinia-plugin-persistedstate'

// 表格展开行进出场动画（捕获期监听，对所有 el-table 生效）
import { installTableExpandMotion } from './plugins/tableExpandMotion'

const app = createApp(App)

app.use(createPinia().use(persistedstate))
app.use(router as unknown as Plugin)
app.use(i18n as unknown as Plugin)
// app.use(CKEditor as unknown as Plugin)

// Element Plus 组件由 unplugin-vue-components 的 ElementPlusResolver 按需
// 引入（importStyle: false，全量 chalk CSS 仍在上方引入）；这里只全局注册
// v-loading 指令——模板里 52 处依赖全局指令，resolver 不覆盖指令。
app.use(ElLoading)

installTableExpandMotion()

app.mount('#app')

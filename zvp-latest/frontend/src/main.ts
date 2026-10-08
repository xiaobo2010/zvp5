import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

import { vLoading } from 'element-plus'
//import * as tmp from './element-plus-components.ts'

//import 'element-plus/theme-chalk/src/index.scss'
import 'element-plus/theme-chalk/display.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import 'element-plus/theme-chalk/base.css'

import 'element-plus/theme-chalk/el-avatar.css'
import 'element-plus/theme-chalk/el-badge.css'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-card.css'
import 'element-plus/theme-chalk/el-col.css'
import 'element-plus/theme-chalk/el-container.css'
import 'element-plus/theme-chalk/el-descriptions.css'
import 'element-plus/theme-chalk/el-descriptions-item.css'
import 'element-plus/theme-chalk/el-dialog.css'
import 'element-plus/theme-chalk/el-divider.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-form-item.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-loading.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-notification.css'
import 'element-plus/theme-chalk/el-overlay.css'
import 'element-plus/theme-chalk/el-page-header.css'
import 'element-plus/theme-chalk/el-pagination.css'
import 'element-plus/theme-chalk/el-progress.css'
import 'element-plus/theme-chalk/el-row.css'
import 'element-plus/theme-chalk/el-select.css'
import 'element-plus/theme-chalk/el-switch.css'
import 'element-plus/theme-chalk/el-tabs.css'
import 'element-plus/theme-chalk/el-tag.css'

import 'virtual:uno.css'

import persistedstate from 'pinia-plugin-persistedstate'

const app = createApp(App)

app.use(createPinia().use(persistedstate))
app.use(router)
app.directive('loading', vLoading)

//for (const com in tmp) app.use(tmp[com])

// app.config.errorHandler = (err, comp, info) => { (err instanceof Error || err instanceof DOMException) && alert(`Error found:\n${err.stack ?? err}`) }

app.mount('#app')

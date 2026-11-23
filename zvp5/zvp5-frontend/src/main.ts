import './assets/styles/element/index.scss'
import { themeHashRedirect } from '@/utils/theme'

import { createApp, type Plugin } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import i18n, { prefetchRemainingLocales } from './i18n'

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
// 服务器管理台专用视觉层：卡片收口 + MAGI 六边形状态单元
import './assets/styles/yg/phi-server.css'
// Phi 遗留普通用户页面皮肤：登记 / 创建 / 详情流程的切角面板与表格
import './assets/styles/yg/phi-legacy-pages.css'
// Phi 主题的 body 级弹层（Teleport 逃出 .yg-phi 的 Dialog/Message/Popover）
import './assets/styles/yg/phi-portals.css'

// 平板滚动流畅层：滚动进行中暂停纯装饰动画（性能规则，不承载视觉设计）
import './assets/styles/perf-tablet.css'

// 渲染模式手感层：个人主页「流畅」档关闭边框流光/六边形底纹/卡片线条
import './assets/styles/smooth-mode.css'

import persistedstate from 'pinia-plugin-persistedstate'

// 表格展开行进出场动画（捕获期监听，对所有 el-table 生效）
import { installTableExpandMotion } from './plugins/tableExpandMotion'

// 低 GPU 档探测：mount 之前给 <html> 打 data-low-gpu，样式层同步降载
import { applyPerfAttributes } from './utils/devicePerf'
// 渲染模式偏好：mount 之前写 data-render-mode，避免先按精细档画一帧
import { installRenderMode } from './utils/renderMode'

// 旧生产包曾注册过 Service Worker；如果当前部署已没有 /sw.js，继续让它
// 控制页面只会一直返回旧入口。先探测脚本，确认缺失后注销并清掉旧缓存。
async function retireLegacyServiceWorker() {
  if (!('serviceWorker' in navigator)) return
  try {
    const registrations = await navigator.serviceWorker.getRegistrations()
    if (!registrations.length) return
    const probe = await fetch('/sw.js', { method: 'HEAD', cache: 'no-store' })
    // 预览 Worker 会把不存在的 /sw.js 回落成 text/html 入口页；只看 ok
    // 会把 SPA HTML 当成 Service Worker 继续保留旧缓存。
    const type = probe.headers.get('content-type') || ''
    if (probe.ok && type.toLowerCase().includes('javascript')) return
    await Promise.all(registrations.map((registration) => registration.unregister()))
    if ('caches' in window) {
      await Promise.all((await caches.keys()).map((key) => caches.delete(key)))
    }
  } catch {
    // 探测/清缓存失败不阻塞页面加载。
  }
}
void retireLegacyServiceWorker()

const themeRedirect = themeHashRedirect(
  window.location.pathname,
  window.location.search,
  window.location.hash
)
if (themeRedirect) {
  window.location.replace(themeRedirect)
}

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

// 必须在 mount 之前：perf-tablet.css 的降载规则依赖 html[data-low-gpu]，
// 若首帧之后再打属性，低端设备会先按满特效渲染一帧再切换，等于白烧一次合成。
applyPerfAttributes()

// 同理：流畅档的覆盖依赖 html[data-render-mode]，首帧前落定才不会闪一下精细版。
installRenderMode()

app.mount('#app')

// 首屏出来之后再把未用到的字典拉下来：不与首屏资源抢带宽，
// 之后点击导航（/ai、/manage、/server、/activity）已是本地命中。
prefetchRemainingLocales()

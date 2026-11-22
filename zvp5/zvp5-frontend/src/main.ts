import './assets/styles/element/index.scss'

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
// 主题变体：运行时主题切换（canary / canary-pro / canary2）
// 必须放在 phi-theme / phi-portals 之后：同名令牌靠后者 + 更高特异性一起取胜。
import './assets/styles/yg/theme-variants.css'

// 主题设计包：canary2（来自 v5-canary2 的 v2 样式库）。全部选择器已收窄到
// html[data-theme-variant='canary2']，不参与 canary 主题的层叠。
import './assets/styles/themes/canary2/tokens.css'
import './assets/styles/themes/canary2/base.css'
import './assets/styles/themes/canary2/materials.css'
import './assets/styles/themes/canary2/typography.css'
import './assets/styles/themes/canary2/controls.css'
import './assets/styles/themes/canary2/liquid.css'
import './assets/styles/themes/canary2/card.css'
import './assets/styles/themes/canary2/patterns.css'
import './assets/styles/themes/canary2/shell.css'
import './assets/styles/themes/canary2/ep-bridge.css'
import './assets/styles/themes/canary2/ux.css'
import './assets/styles/themes/canary2/glass.css'
import './assets/styles/themes/canary2/table.css'
// canary2 适配层：--yg-* / .yg-* / EP 组件 → yg2 浅色玻璃令牌
import './assets/styles/themes/canary2/adapt.css'

// 主题设计包：canary-pro（来自 v5-canary-pro 的工作台令牌与基线）
import './assets/styles/themes/canary-pro/base.css'
// canary-pro 适配层：--yg-* / .yg-* / EP 组件 → 深色工作台玻璃令牌
import './assets/styles/themes/canary-pro/adapt.css'

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
// 主题变体偏好：mount 之前写 data-theme-variant，避免首帧闪回默认主题
import { installTheme } from './utils/theme'

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

// 同理：主题变体依赖 html[data-theme-variant]，首帧前落定避免闪回默认主题。
installTheme()

app.mount('#app')

// 首屏出来之后再把未用到的字典拉下来：不与首屏资源抢带宽，
// 之后点击导航（/ai、/manage、/server、/activity）已是本地命中。
prefetchRemainingLocales()

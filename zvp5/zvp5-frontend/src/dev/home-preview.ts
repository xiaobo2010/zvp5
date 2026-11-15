import { createApp } from 'vue'
import HomePreview from './HomePreview.vue'
import '../assets/styles/yg/main.css'

// 独立开发入口：不加载真实路由、登录状态、API 或设备检测。
if (import.meta.env.DEV) createApp(HomePreview).mount('#app')

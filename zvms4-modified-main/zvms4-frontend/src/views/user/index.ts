export { default as UserActivity } from './UserActivity.vue'
export { default as UserHome } from './UserHome.vue'
export { default as UserLogin } from './UserLogin.vue'
export { default as UserNav } from './UserNav.vue'

// 修改密码对话框与视图组件解耦，避免 import 该函数时连带打包全部视图
export { modifyPasswordDialogs } from './modifyPasswordDialogs'

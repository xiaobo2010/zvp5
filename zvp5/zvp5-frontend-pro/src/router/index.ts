import { createRouter, createWebHashHistory } from 'vue-router'
import { isLoggedIn } from '@/api/canary'
import { hasAnyRole } from '@/api/permissions'

const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: '首页' }
    },
    {
      path: '/me',
      name: 'profile',
      component: () => import('@/views/ProfileView.vue'),
      meta: { title: '个人主页', requiresAuth: true }
    },
    {
      path: '/volunteers',
      name: 'volunteers',
      component: () => import('@/views/VolunteerListView.vue'),
      meta: { title: '义工列表', requiresAuth: true }
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '登录' }
    },
    {
      path: '/volunteers/create',
      name: 'volunteer-create',
      component: () => import('@/views/AddVolunteerView.vue'),
      meta: { title: '义工登记', requiresAuth: true, roles: ['admin', 'department'] }
    },
    {
      path: '/volunteers/create/new',
      name: 'volunteer-create-new',
      component: () => import('@/views/ActivityCreateView.vue'),
      meta: { title: '创建义工', requiresAuth: true, roles: ['admin', 'department'] }
    },
    {
      path: '/volunteers/create/:mode(add|modify)',
      name: 'volunteer-register',
      component: () => import('@/views/ActivityRegisterView.vue'),
      meta: { title: '义工登记', requiresAuth: true, roles: ['admin', 'department'] }
    },
    {
      path: '/volunteers/:id',
      name: 'volunteer-detail',
      component: () => import('@/views/VolunteerDetailView.vue'),
      meta: { title: '义工详情', requiresAuth: true }
    },
    {
      path: '/server',
      name: 'server',
      component: () => import('@/views/ServerView.vue'),
      meta: { title: '服务器管理', requiresAuth: true, roles: ['admin'] }
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('@/views/ManageView.vue'),
      meta: { title: '平台管理', requiresAuth: true, roles: ['admin', 'department', 'secretary'] }
    },
    {
      path: '/manage/users/:id',
      name: 'manage-user',
      component: () => import('@/views/ManageUserView.vue'),
      meta: {
        title: '用户资料',
        requiresAuth: true,
        roles: ['admin', 'department', 'secretary']
      }
    },
    {
      path: '/ai',
      name: 'ai-gateway',
      component: () => import('@/views/AiGatewayView.vue'),
      meta: { title: 'AI 网关', requiresAuth: true, roles: ['admin', 'department'] }
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
      meta: { title: '通知', requiresAuth: true }
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('@/views/AboutView.vue'),
      meta: { title: '关于' }
    },
    {
      path: '/404',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '页面不存在' }
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/404'
    }
  ],
  scrollBehavior: (to) => to.hash ? { el: to.hash, top: 16 } : { top: 0 }
})

router.beforeEach((to) => {
  const auth = isLoggedIn()
  const roles = Array.isArray(to.meta.roles) ? (to.meta.roles as string[]) : []
  // hash router 的 ``to.fullPath`` 可能自带 `#/`；把它塞进 query 会变成
  // `/login?redirect=/me%23/`，登录后再跳回错误地址。只保留路径和查询串。
  const redirect = to.fullPath.split('#')[0] || to.path
  if (to.meta.requiresAuth && !auth) {
    return { path: '/login', query: { redirect } }
  }
  if (roles.length > 0) {
    if (!auth) return { path: '/login', query: { redirect } }
    if (!hasAnyRole(...roles)) return { path: '/404', replace: true }
  }
  return true
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · ZVMS` : 'ZVMS'
})

export default router

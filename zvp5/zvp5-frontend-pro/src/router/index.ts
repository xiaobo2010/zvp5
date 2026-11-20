import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
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
      meta: { title: '个人主页' }
    },
    {
      path: '/volunteers',
      name: 'volunteers',
      component: () => import('@/views/VolunteerListView.vue'),
      meta: { title: '义工列表' }
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
      meta: { title: '义工登记' }
    },
    {
      path: '/volunteers/create/new',
      name: 'volunteer-create-new',
      component: () => import('@/views/ActivityCreateView.vue'),
      meta: { title: '创建义工' }
    },
    {
      path: '/volunteers/create/:mode(add|modify)',
      name: 'volunteer-register',
      component: () => import('@/views/ActivityRegisterView.vue'),
      meta: { title: '义工登记' }
    },
    {
      path: '/server',
      name: 'server',
      component: () => import('@/views/ServerView.vue'),
      meta: { title: '服务器管理' }
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('@/views/ManageView.vue'),
      meta: { title: '平台管理' }
    },
    {
      path: '/ai',
      name: 'ai-gateway',
      component: () => import('@/views/AiGatewayView.vue'),
      meta: { title: 'AI 网关' }
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
      meta: { title: '通知' }
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
  scrollBehavior: () => ({ top: 0 })
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · ZVMS` : 'ZVMS'
})

export default router

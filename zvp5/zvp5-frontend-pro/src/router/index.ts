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

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      redirect: () => (localStorage.getItem('token') ? '/user' : '/user/login')
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('../views/AboutView.vue')
    },
    {
      path: '/changelog',
      name: 'changelog',
      component: () => import('../views/ChangelogView.vue')
    },
    {
      path: '/user/login',
      name: 'user-login',
      component: () => import('../views/user/UserLogin.vue')
    },
    {
      path: '/user',
      name: 'user',
      component: () => import('../views/user/UserHome.vue')
    },
    {
      path: '/user/:id',
      name: 'user-page',
      component: () => import('../views/user/UserPage.vue')
    },
    {
      path: '/user/:id/:action',
      name: 'user-action',
      component: () => import('../views/user/UserPage.vue')
    },
    {
      path: '/activity/create',
      name: 'activity-create',
      component: () => import('../views/activity/CreateActivity.vue'),
      children: [
        {
          path: '/activity/create',
          name: 'activity-create-home',
          component: () => import('../views/activity/CreatePage.vue')
        },
        {
          path: '/activity/create/:type',
          name: 'activity-create-type',
          component: () => import('../views/activity/CreatePage.vue')
        }
      ]
    },
    {
      path: '/activities',
      name: 'activity',
      component: () => import('../views/user/UserActivity.vue')
    },
    {
      path: '/activities/:mode',
      name: 'activity-mode',
      component: () => import('../views/user/UserActivity.vue')
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('../views/manage/ManageHome.vue')
    },
    {
      path: '/manage/:action',
      name: 'manage-action',
      component: () => import('../views/manage/ManageHome.vue')
    },
    {
      path: '/group/:id',
      name: 'group-user',
      component: () => import('../views/group/GroupPage.vue')
    },
    {
      path: '/group/:id/:action',
      name: 'group-action',
      component: () => import('../views/group/GroupPage.vue')
    },
    {
      path: '/activity/details/:id',
      name: 'activity-view',
      component: () => import('../views/activity/ActivityPage.vue')
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFound.vue')
    },
  ]
})

// 学海平板 / 资料中心以 `/index.html` 作为应用入口 URL（旧版 hash 路由会忽略该路径，
// 切换到 history 路由后它会命中 `/:pathMatch(.*)*` 渲染 404 页）。统一归一化到 SPA 根。
// 勿在此处取消「所有在飞请求」：组件首个请求（onMounted）与触发导航的
// router.replace 同 tick 发出，渲染 flush 先于守卫，会掐掉新页面自己的请求。
router.beforeEach((to) => {
  if (to.path === '/index.html' || to.path.endsWith('/index.html')) {
    return { path: '/', replace: true }
  }
})

export default router

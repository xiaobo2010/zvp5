import { createRouter, createWebHashHistory, useRouter } from 'vue-router'
import { isXuehaiDCenter } from '@/plugins/ua.ts'

const isXHDC = isXuehaiDCenter(false)

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      redirect: isXHDC ? undefined : '/user/login',
      component: isXHDC ? {
          setup() {
              useRouter().push('/user/login')
          }
      } : undefined
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('../views/AboutView.vue')
    },
    {
      path: '/user/login',
      name: 'user-login',
      component: () => import('../views/user/UserLogin.vue')
    },
    {
      path: '/user',
      name: 'user-home',
      component: () => import('../views/user/UserHome.vue')
    },
    {
      path: '/me',
      name: 'my-profile',
      component: () => import('../views/user/UserProfile.vue')
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
      path: '/activities',
      name: 'activity-list',
      component: () => import('../views/activity/ActivityList.vue')
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('../views/manage/ManageHome.vue')
    },
    {
      path: '/manage/roles/:role',
      name: 'manage-role',
      component: () => import('../views/manage/ManageRole.vue')
    },
    {
      path: '/group/:group',
      name: 'group-user',
      component: () => import('../views/manage/ManageGroup.vue')
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

export default router

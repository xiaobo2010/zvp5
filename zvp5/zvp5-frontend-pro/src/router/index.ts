import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue')
    },
    {
      path: '/me',
      name: 'profile',
      component: () => import('@/views/ProfileView.vue')
    },
    {
      path: '/volunteers',
      name: 'volunteers',
      component: () => import('@/views/VolunteerListView.vue')
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue')
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/'
    }
  ],
  scrollBehavior: () => ({ top: 0 })
})

export default router

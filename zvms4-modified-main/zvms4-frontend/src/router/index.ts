import {
  createRouter,
  createWebHistory,
  type LocationQuery,
  type RouteLocationRaw
} from 'vue-router'
import { maintenanceGuard } from './maintenanceGuard'

// 登记入口页的分流：带 activity 的深链（活动详情、旧书签）直达「添加义工」，
// 并把整份 query 原样带走，避免 activity/session 等参数在分流时丢失。
export function registerEntryTarget(query: LocationQuery): RouteLocationRaw | null {
  const raw = query.activity
  const activity = Array.isArray(raw)
    ? raw.find((value): value is string => typeof value === 'string' && value.trim().length > 0)
    : raw
  if (typeof activity !== 'string' || !activity.trim()) return null
  return {
    name: 'activity-register-mode',
    params: { mode: 'create' },
    query,
    replace: true
  }
}

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
      component: () => import('../views/user-v2/AboutView.vue')
    },
    {
      path: '/user/login',
      name: 'user-login',
      component: () => import('../views/user-v2/UserLogin.vue')
    },
    {
      path: '/user',
      name: 'user',
      component: () => import('../views/user-v2/UserHome.vue')
    },
    {
      path: '/me',
      name: 'profile',
      component: () => import('../views/user-v2/UserProfile.vue')
    },
    {
      path: '/user/:id',
      name: 'user-page',
      component: () => import('../views/user-v2/UserPage.vue')
    },
    {
      path: '/user/:id/:action',
      name: 'user-action',
      component: () => import('../views/user-v2/UserPage.vue')
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
      // 登记入口：先选「添加义工」还是「修改义工」，再进入同一个编辑器。
      // 旧深链 /activity/register?activity=ID 在 beforeEnter 里直达添加模式并保留 query。
      path: '/activity/register',
      name: 'activity-register',
      component: () => import('../views/activity/VolunteerRegisterEntry.vue'),
      beforeEnter: (to) => registerEntryTarget(to.query) ?? true
    },
    {
      // 登记编辑器：URL 是模式的唯一事实来源，create/edit 各自可分享、可前进后退。
      path: '/activity/register/:mode(create|edit)',
      name: 'activity-register-mode',
      component: () => import('../views/activity/VolunteerRegister.vue')
    },
    {
      path: '/activities',
      name: 'activity',
      component: () => import('../views/user-v2/UserActivity.vue')
    },
    {
      path: '/activities/:mode',
      name: 'activity-mode',
      component: () => import('../views/user-v2/UserActivity.vue')
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('../views/manage/ManageHome.vue')
    },
    {
      path: '/maintenance',
      name: 'maintenance',
      component: () => import('../views/MaintenanceView.vue')
    },
    {
      path: '/manage/:action',
      name: 'manage-action',
      component: () => import('../views/manage/ManageHome.vue')
    },
    {
      path: '/ai',
      name: 'ai',
      redirect: '/ai/chat'
    },
    {
      path: '/ai/:action',
      name: 'ai-action',
      component: () => import('../views/ai/AIHome.vue')
    },
    {
      path: '/server',
      name: 'server',
      component: () => import('../views/server/ServerHome.vue'),
      redirect: '/server/maintenance',
      children: [
        {
          path: 'maintenance',
          name: 'server-maintenance',
          component: () => import('../views/server/ServerMaintenance.vue')
        },
        {
          path: 'database',
          name: 'server-database',
          component: () => import('../views/server/ServerDatabase.vue')
        },
        {
          path: 'totp',
          name: 'server-totp',
          component: () => import('../views/server/ServerTotp.vue')
        },
        {
          path: 'ssh',
          name: 'server-ssh',
          component: () => import('../views/server/ServerSsh.vue')
        },
        {
          path: 'files',
          name: 'server-files',
          component: () => import('../views/server/ServerFiles.vue')
        },
        {
          path: 'db/content',
          name: 'server-db-content',
          component: () => import('../views/server/ServerDbContent.vue')
        }
      ]
    },
    {
      // 兼容旧 /server/:action 深链:已知子页由静态子路由优先生效,其余统一归到维护页
      path: '/server/:action',
      name: 'server-legacy-redirect',
      redirect: '/server/maintenance'
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
// 维护模式与入口归一化共用同一个全局守卫:应用内导航、深链接、冷启动都会被覆盖。
router.beforeEach((to) => {
  if (to.path === '/index.html' || to.path.endsWith('/index.html')) {
    return { path: '/', replace: true }
  }
  return maintenanceGuard(to)
})

export default router

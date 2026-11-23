import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import type { UserRole } from '@/types/domain'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import { i18n } from '@/shared/i18n'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    titleKey?: string
    roles?: UserRole[]
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/auth/LoginPage.vue'),
    meta: { public: true, titleKey: 'login.submit' }
  },
  {
    path: '/maintenance',
    name: 'maintenance',
    component: () => import('@/pages/system/MaintenancePage.vue'),
    meta: { public: true, titleKey: 'maintenance.title' }
  },
  {
    path: '/',
    component: () => import('@/app/AppShell.vue'),
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/pages/home/HomePage.vue'),
        meta: { titleKey: 'nav.home' }
      },
      {
        path: 'activities',
        name: 'activities',
        component: () => import('@/pages/activities/ActivitiesPage.vue'),
        meta: { titleKey: 'nav.activities' }
      },
      {
        path: 'activities/:id',
        name: 'activity-detail',
        component: () => import('@/pages/activities/ActivityDetailPage.vue'),
        meta: { titleKey: 'activities.detail' }
      },
      {
        path: 'notifications',
        name: 'notifications',
        component: () => import('@/pages/notifications/NotificationsPage.vue'),
        meta: { titleKey: 'nav.notifications' }
      },
      {
        path: 'me',
        name: 'profile',
        component: () => import('@/pages/profile/ProfilePage.vue'),
        meta: { titleKey: 'nav.profile' }
      },
      {
        path: 'manage',
        name: 'manage',
        component: () => import('@/pages/manage/ManagePage.vue'),
        meta: {
          titleKey: 'nav.manage',
          roles: ['admin', 'department']
        }
      },
      {
        path: 'manage/users',
        name: 'manage-users',
        component: () => import('@/pages/manage/UsersPage.vue'),
        meta: {
          titleKey: 'manage.users.title',
          roles: ['admin', 'department']
        }
      },
      {
        path: 'manage/groups',
        name: 'manage-groups',
        component: () => import('@/pages/manage/GroupsPage.vue'),
        meta: {
          titleKey: 'manage.groups.title',
          roles: ['admin', 'department']
        }
      },
      {
        path: 'manage/notifications',
        name: 'manage-notifications',
        component: () => import('@/pages/manage/NotifyPage.vue'),
        meta: { titleKey: 'manage.notify.title' }
      },
      {
        path: 'manage/logs',
        name: 'manage-logs',
        component: () => import('@/pages/manage/LogsPage.vue'),
        meta: { titleKey: 'manage.logs.title' }
      },
      {
        path: 'server/:section?',
        name: 'server',
        component: () => import('@/pages/system/ServerPage.vue'),
        meta: { titleKey: 'nav.server', roles: ['admin'] }
      },
      {
        path: 'ai/:section?',
        name: 'ai',
        component: () => import('@/pages/ai/AgentPage.vue'),
        meta: { titleKey: 'nav.ai', roles: ['admin', 'department', 'secretary'] }
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/pages/system/NotFoundPage.vue'),
    meta: { public: true, titleKey: 'notFound.title' }
  }
]

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(to, _from, saved) {
    if (saved) return saved
    if (to.hash) return { el: to.hash, behavior: 'smooth' }
    return { top: 0 }
  }
})

router.beforeEach((to) => {
  const session = useSessionStore()
  const ui = useUiStore()

  if (to.name === 'login' && session.authenticated) {
    return { name: 'home' }
  }

  if (to.meta.public) return true

  if (!session.authenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.meta.roles && !to.meta.roles.some((role) => session.roles.includes(role))) {
    ui.notify(i18n.global.t('notFound.desc'), 'danger')
    return { name: 'home' }
  }

  return true
})

router.afterEach((to) => {
  const key = to.meta.titleKey
  const title = typeof key === 'string' ? i18n.global.t(key) : ''
  document.title = title ? `${title} · ZVP5` : 'ZVP5 · 义工平台'
})

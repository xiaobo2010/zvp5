import type { Component } from 'vue'
import {
  HomeFilled,
  CirclePlusFilled,
  EditPen,
  Management,
  Avatar,
  InfoFilled,
  Monitor,
  ChatLineRound
} from '@element-plus/icons-vue'
import MdiEye from '@/icons/MdiEye.vue'

export interface NavItem {
  /** 稳定的语义 key，也用于 i18n 的 nav.* 文案 */
  key: string
  /** 路由地址；manage 走特殊跳转 */
  path: string
  icon: Component
  /** 是否显示，入参是用户身份数组 */
  show: (position: string[]) => boolean
  /** 当前路径是否命中该项 */
  judge: (path: string) => boolean
}

export interface NavGroup {
  key: string
  labelKey: string
  items: NavItem[]
}

const all = () => true
const hasRole = (role: string) => (position: string[]) => position.includes(role)
const hasAnyStaff = (position: string[]) => position.filter((x) => x !== 'student').length > 0

export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'overview',
    labelKey: 'nav.groups.overview',
    items: [
      {
        key: 'home',
        path: '/user',
        icon: HomeFilled,
        show: all,
        judge: (path) => path === '/user'
      },
      {
        key: 'activity',
        path: '/activities/mine',
        icon: MdiEye,
        show: all,
        judge: (path) =>
          (path.startsWith('/activity') &&
            !path.startsWith('/activity/create') &&
            !path.startsWith('/activity/register')) ||
          path.startsWith('/activities')
      }
    ]
  },
  {
    key: 'collab',
    labelKey: 'nav.groups.collab',
    items: [
      {
        key: 'create',
        path: '/activity/create',
        icon: CirclePlusFilled,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/activity/create')
      },
      {
        key: 'register',
        path: '/activity/register',
        icon: EditPen,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/activity/register')
      },
      {
        key: 'ai',
        path: '/ai/chat',
        icon: ChatLineRound,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/ai')
      }
    ]
  },
  {
    key: 'admin',
    labelKey: 'nav.groups.admin',
    items: [
      {
        key: 'manage',
        path: '/management',
        icon: Management,
        show: hasAnyStaff,
        judge: (path) =>
          path.startsWith('/group') ||
          (path.startsWith('/user/') && path !== '/user/login') ||
          path.startsWith('/manage')
      },
      {
        key: 'server',
        path: '/server',
        icon: Monitor,
        show: hasRole('admin'),
        judge: (path) => path.startsWith('/server')
      }
    ]
  }
]

/** 侧边栏底部的固定入口 */
export const FOOT_ITEMS: NavItem[] = [
  {
    key: 'about',
    path: '/about',
    icon: InfoFilled,
    show: all,
    judge: (path) => path.startsWith('/about')
  },
  {
    key: 'profile',
    path: '/me',
    icon: Avatar,
    show: all,
    judge: (path) => path.startsWith('/me')
  }
]

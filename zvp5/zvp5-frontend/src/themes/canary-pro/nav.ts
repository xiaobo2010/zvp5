import type { Component } from 'vue'
import {
  HomeFilled,
  Calendar,
  Avatar,
  Bell,
  Management,
  Monitor,
  ChatLineRound,
  InfoFilled,
  CirclePlusFilled,
  EditPen
} from '@element-plus/icons-vue'

export interface ProNavItem {
  key: string
  label: string
  caption: string
  path: string
  icon: Component
  show: (position: string[]) => boolean
  judge: (path: string) => boolean
}

export interface ProNavGroup {
  key: string
  label: string
  items: ProNavItem[]
}

const all = () => true
const staff = (position: string[]) => position.filter((x) => x !== 'student').length > 0

/**
 * canary-pro 的工作台导航：左侧固定栏、按工作区/管理分组。
 * 与 canary2 的分组侧边栏是两套结构：这里是「工作台 + 模块」，
 * 路径全部对齐当前 router。
 */
export const PRO_NAV_GROUPS: ProNavGroup[] = [
  {
    key: 'workspace',
    label: '工作台',
    items: [
      {
        key: 'home',
        label: '概览',
        caption: 'Overview',
        path: '/user',
        icon: HomeFilled,
        show: all,
        judge: (path) => path === '/user'
      },
      {
        key: 'me',
        label: '个人主页',
        caption: 'Profile',
        path: '/me',
        icon: Avatar,
        show: all,
        judge: (path) => path.startsWith('/me')
      },
      {
        key: 'notifications',
        label: '通知中心',
        caption: 'Inbox',
        path: '/notifications',
        icon: Bell,
        show: all,
        judge: (path) => path.startsWith('/notifications')
      },
      {
        key: 'activity',
        label: '义工活动',
        caption: 'Activities',
        path: '/activities',
        icon: Calendar,
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
    key: 'operate',
    label: '运营',
    items: [
      {
        key: 'create',
        label: '创建活动',
        caption: 'Create',
        path: '/activity/create',
        icon: CirclePlusFilled,
        show: (position) =>
          position.includes('admin') ||
          position.includes('department') ||
          position.includes('secretary'),
        judge: (path) => path.startsWith('/activity/create')
      },
      {
        key: 'register',
        label: '义工登记',
        caption: 'Register',
        path: '/activity/register',
        icon: EditPen,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/activity/register')
      },
      {
        key: 'ai',
        label: 'AI 网关',
        caption: 'Gateway',
        path: '/ai',
        icon: ChatLineRound,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/ai')
      }
    ]
  },
  {
    key: 'console',
    label: '控制台',
    items: [
      {
        key: 'manage',
        label: '平台管理',
        caption: 'Manage',
        path: '/manage',
        icon: Management,
        show: staff,
        judge: (path) =>
          path.startsWith('/group') ||
          (path.startsWith('/user/') && path !== '/user/login') ||
          path.startsWith('/manage')
      },
      {
        key: 'server',
        label: '服务器',
        caption: 'Server',
        path: '/server',
        icon: Monitor,
        show: (position) => position.includes('admin'),
        judge: (path) => path.startsWith('/server')
      },
      {
        key: 'about',
        label: '关于',
        caption: 'About',
        path: '/about',
        icon: InfoFilled,
        show: all,
        judge: (path) => path.startsWith('/about')
      }
    ]
  }
]

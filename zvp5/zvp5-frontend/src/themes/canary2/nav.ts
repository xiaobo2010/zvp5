import type { Component } from 'vue'
import {
  HomeFilled,
  CirclePlusFilled,
  EditPen,
  Management,
  Avatar,
  InfoFilled,
  Monitor,
  ChatLineRound,
  Bell
} from '@element-plus/icons-vue'
import MdiEye from '@/icons/MdiEye.vue'

export interface NavItem {
  /** 稳定的语义 key */
  key: string
  /** i18n key（`nav.*`）；缺失时回落到 label */
  labelKey: string
  /** 中文兜底文案（平台强制 zh-CN，但保留 key 供后续多语言） */
  label: string
  /** 当前应用的路由地址 */
  path: string
  icon: Component
  /** 是否显示，入参是用户身份数组 */
  show: (position: string[]) => boolean
  /** 当前路径是否命中该项 */
  judge: (path: string) => boolean
}

export interface NavGroup {
  key: string
  label: string
  items: NavItem[]
}

const all = () => true
const hasRole = (role: string) => (position: string[]) => position.includes(role)
const hasAnyStaff = (position: string[]) => position.filter((x) => x !== 'student').length > 0

/**
 * canary2 的导航结构：分组侧边栏。
 * 路径全部对齐当前 `src/router/index.ts`，不沿用分支里的 /management、
 * /user/login 等旧地址。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'overview',
    label: '总览',
    items: [
      {
        key: 'home',
        labelKey: 'nav.home',
        label: '首页',
        path: '/user',
        icon: HomeFilled,
        show: all,
        judge: (path) => path === '/user'
      },
      {
        key: 'activity',
        labelKey: 'nav.activity',
        label: '义工列表',
        path: '/activities',
        icon: MdiEye,
        show: all,
        judge: (path) =>
          (path.startsWith('/activity') &&
            !path.startsWith('/activity/create') &&
            !path.startsWith('/activity/register')) ||
          path.startsWith('/activities')
      },
      {
        key: 'notifications',
        labelKey: 'nav.notifications',
        label: '站内通知',
        path: '/notifications',
        icon: Bell,
        show: (position) => !position.includes('admin'),
        judge: (path) => path.startsWith('/notifications')
      }
    ]
  },
  {
    key: 'collab',
    label: '协作',
    items: [
      {
        key: 'create',
        labelKey: 'nav.create',
        label: '创建义工',
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
        labelKey: 'nav.register',
        label: '添加/修改义工',
        path: '/activity/register',
        icon: EditPen,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/activity/register')
      },
      {
        key: 'ai',
        labelKey: 'nav.ai',
        label: 'AI 网关',
        path: '/ai',
        icon: ChatLineRound,
        show: (position) => position.includes('admin') || position.includes('department'),
        judge: (path) => path.startsWith('/ai')
      }
    ]
  },
  {
    key: 'admin',
    label: '管理',
    items: [
      {
        key: 'manage',
        labelKey: 'nav.manage',
        label: '平台管理',
        path: '/manage',
        icon: Management,
        show: hasAnyStaff,
        judge: (path) =>
          path.startsWith('/group') ||
          (path.startsWith('/user/') && path !== '/user/login') ||
          path.startsWith('/manage')
      },
      {
        key: 'server',
        labelKey: 'nav.server',
        label: '服务器管理',
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
    labelKey: 'nav.about',
    label: '关于',
    path: '/about',
    icon: InfoFilled,
    show: all,
    judge: (path) => path.startsWith('/about')
  },
  {
    key: 'profile',
    labelKey: 'nav.profile',
    label: '我的',
    path: '/me',
    icon: Avatar,
    show: all,
    judge: (path) => path.startsWith('/me')
  }
]

/**
 * 首屏字典：始终挂载的组件（App 外壳 / UserNav / 通知提醒 / 封禁屏 / 登录揭示 /
 * 主设备闸）与登录、关于、维护页会用到的消息，必须随入口静态打进来。
 *
 * 其余 4 个大字典（ai / manage / server / activity）走 `namespaces.ts` 的
 * 路由懒加载，别把它们的静态 import 挪回本文件或入口链上的任何模块。
 */
import * as about from './about'
import * as common from './common'
import * as device from './device'
import * as home from './home'
import * as log from './log'
import * as login from './login'
import * as maintenance from './maintenance'
import * as markdown from './markdown'
import * as nav from './nav'
import * as notificationCenter from './notificationCenter'
import * as profile from './profile'
import * as user from './user'
import * as validation from './validation'

export const coreZhCN = {
  about: about.zhCN,
  common: common.zhCN,
  device: device.zhCN,
  home: home.zhCN,
  log: log.zhCN,
  login: login.zhCN,
  maintenance: maintenance.zhCN,
  markdown: markdown.zhCN,
  nav: nav.zhCN,
  notificationCenter: notificationCenter.zhCN,
  profile: profile.zhCN,
  user: user.zhCN,
  validation: validation.zhCN
}

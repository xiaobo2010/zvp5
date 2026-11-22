/**
 * 主题注册表：三个主题是三套完全不同的设计包，不是配色变体。
 * 切换主题时，整个应用的外壳、导航结构、卡片层次都必须改变。
 *
 * - canary: Phi 设计（当前主分支）——六边形底纹、速度线、棱角卡片、UserNav
 * - canary2: yg2 设计（来自 v5-canary2）——光场、玻璃左侧栏、圆角卡片、AmbientField
 * - canary-pro: 工作台设计（来自 v5-canary-pro）——固定左侧栏、顶栏、深色玻璃卡片
 *
 * 新增主题 = 复制一份设计包到 `src/themes/<id>/`，再在这里加一条。
 */

import { defineAsyncComponent, type Component } from 'vue'
import CanaryShell from './canary/CanaryShell.vue'

export interface ThemeShell {
  /** 主题唯一标识（与后端 theme registry 的 id 一致） */
  id: string
  /** 展示名称 */
  label: string
  /** 一句话说明，用于设置页/文档 */
  description: string
  /** 外壳组件：导航/顶栏/侧边栏等整个应用框架，内容通过默认插槽传入 */
  shell: Component
  /** 外壳根节点上的设计类，便于调试和 e2e 定位 */
  className: string
  /** 是否需要底部留白（竖屏底部 dock 等） */
  needsBottomInset: boolean
  /** 登录页/维护页是否沿用外壳（false = 全屏页面，不套登录后导航） */
  shellForAuthPages: boolean
}

/**
 * 所有已注册的主题。
 * 默认的 canary 静态引入，保证现网首屏与改造前一致、不出现异步外壳的空白帧；
 * canary2 / canary-pro 按需加载，只在用户选择后拉取各自的设计包。
 */
export const THEME_REGISTRY: ThemeShell[] = [
  {
    id: 'canary',
    label: 'Canary',
    description: 'Phi 设计：六边形底纹、速度线、棱角卡片与玻璃药丸导航',
    shell: CanaryShell,
    className: 'theme-canary',
    needsBottomInset: true,
    shellForAuthPages: false
  },
  {
    id: 'canary2',
    label: 'Canary 2',
    description: 'yg2 设计：环境光场、分组玻璃侧栏、顶栏与浅色圆角卡片',
    shell: defineAsyncComponent(() => import('./canary2/Canary2Shell.vue')),
    className: 'theme-canary2',
    needsBottomInset: true,
    shellForAuthPages: false
  },
  {
    id: 'canary-pro',
    label: 'Canary Pro',
    description: '工作台设计：固定左侧栏、面包屑顶栏、深色玻璃卡片',
    shell: defineAsyncComponent(() => import('./canary-pro/CanaryProShell.vue')),
    className: 'theme-canary-pro',
    needsBottomInset: false,
    shellForAuthPages: false
  }
]

export const DEFAULT_THEME_ID = 'canary'

export const THEME_IDS = THEME_REGISTRY.map((theme) => theme.id)

export function getThemeShell(themeId: string): ThemeShell {
  return THEME_REGISTRY.find((t) => t.id === themeId) ?? THEME_REGISTRY[0]
}

export function isValidTheme(themeId: string): boolean {
  return THEME_REGISTRY.some((t) => t.id === themeId)
}

/**
 * Motion 全局参数。
 *
 * 时间尺度与 CSS tokens（design/tokens.css）保持一致：
 * - 微交互 180ms、组件过渡 280ms、弹窗 420ms、页面 340ms；
 * - 曲线优先用弹簧（物理参数），CSS 侧用 --spring-* 的 linear() 积分曲线。
 *
 * 只把「需要真实物理惯性」的动效交给 JS（导航选中滑块、弹窗进出），
 * 其余 hover / 颜色 / 阴影继续走 CSS，避免为了统一而全站跑 JS 动画。
 */

export const springs = {
  /** 导航滑块、开关一类的短距离位移 */
  snappy: { type: 'spring', stiffness: 520, damping: 38, mass: 0.9 },
  /** 弹窗、抽屉一类的进出场 */
  soft: { type: 'spring', stiffness: 320, damping: 34, mass: 1 },
  /** 需要一点回弹的强调动作（图标弹出、Toast） */
  bouncy: { type: 'spring', stiffness: 480, damping: 26, mass: 1 }
} as const

export const fades = {
  /** 遮罩 */
  scrim: { duration: 0.22, ease: [0.22, 1, 0.36, 1] },
  /** 轻量淡入 */
  quick: { duration: 0.18, ease: [0.22, 1, 0.36, 1] }
} as const

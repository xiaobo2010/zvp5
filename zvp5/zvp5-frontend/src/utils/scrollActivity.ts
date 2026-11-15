/**
 * 滚动状态标志：滚动进行中给 `<html>` 打 `data-scrolling`，静止约 160ms 后移除。
 *
 * 用途（配合 `assets/styles/perf-tablet.css`）：目标设备是三星 P615C/P620
 * （Tab S6 Lite，Mali 级 GPU + 60Hz），滚动帧预算被固定毛玻璃导航的
 * backdrop-filter 采样挤占时，全屏 3D 网格、速度线、判定线等纯装饰动画
 * 仍在并行占用合成带宽，是滚动断裂感的主要来源。滚动中的装饰动画处于
 * 感知盲区，冻结 160ms 肉眼不可辨，样式层据此暂停动画，把帧还给滚动。
 *
 * 设计约束：
 *   * 监听挂在 window 的捕获阶段——主滚动容器是 `.yg-layout__content`
 *     （flat.css「固定视口 + 内容区滚动」），el-table、弹层、SSH 终端等
 *     都在各自的内部容器里滚，只有捕获能一网打尽。
 *   * `passive: true`：只读，绝不 preventDefault。
 *   * 每次事件只做「标志位短路 + 定时器重置」，高频滚动事件下的开销
 *     接近于零；写 DOM 属性仅在状态翻转时发生。
 */

const REMOVE_DELAY_MS = 160

let active = false
let removeTimer: number | null = null

function writeFlag(present: boolean): void {
  const root = document.documentElement
  if (present) root.dataset.scrolling = ''
  else delete root.dataset.scrolling
}

function handleScroll(): void {
  if (!active) {
    active = true
    writeFlag(true)
  }
  if (removeTimer !== null) window.clearTimeout(removeTimer)
  removeTimer = window.setTimeout(() => {
    removeTimer = null
    active = false
    writeFlag(false)
  }, REMOVE_DELAY_MS)
}

export function installScrollActivityFlag(): void {
  window.addEventListener('scroll', handleScroll, { capture: true, passive: true })
}

export function uninstallScrollActivityFlag(): void {
  window.removeEventListener('scroll', handleScroll, { capture: true })
  if (removeTimer !== null) {
    window.clearTimeout(removeTimer)
    removeTimer = null
  }
  if (active) {
    active = false
    writeFlag(false)
  }
}

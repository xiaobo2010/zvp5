import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/base.css'

// 静态托管只有根站回落时，旧 history 深链接改写到当前子应用的 hash 路由。
const base = import.meta.env.BASE_URL
const prefix = base === '/' ? '' : base.replace(/\/$/, '')
const rest = window.location.pathname.startsWith(`${prefix}/`)
  ? window.location.pathname.slice(prefix.length + 1)
  : ''
if (rest && rest !== 'index.html') {
  const url = new URL(window.location.href)
  const query = url.search
  url.pathname = base
  url.search = ''
  url.hash = `#/${rest}${query}${url.hash}`
  window.location.replace(url)
}

/**
 * 平板/触屏没有 hover，且 iOS Safari 只有在文档上挂过触摸监听时才稳定触发
 * ``:active``。给按下的按钮补一个短命的类，让按压动画在抬手后仍能放完，
 * 而不是被一次快速 tap 吞掉。
 */
function installTouchPressFeedback() {
  if (!window.matchMedia || !window.matchMedia('(hover: none), (pointer: coarse)').matches) {
    return
  }

  const SELECTOR =
    'button, a[href], [role="button"], .liquid-btn, .register-entry__choice'
  let pressed: HTMLElement | null = null

  const release = () => {
    pressed?.classList.remove('is-touch-pressed')
    pressed = null
  }

  window.addEventListener(
    'pointerdown',
    (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return
      const target = (event.target as Element | null)?.closest<HTMLElement>(SELECTOR)
      if (!target) return
      if (target.matches(':disabled, [aria-disabled="true"]')) return

      release()
      pressed = target
      target.classList.add('is-touch-pressed')
      window.setTimeout(release, 420)
    },
    { passive: true }
  )

  window.addEventListener('pointercancel', release, { passive: true })
}

installTouchPressFeedback()

createApp(App).use(router).mount('#app')

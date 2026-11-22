/* 全局主题切换器（整站多构建版）。
 *
 * 主题不是「同一套页面换外壳」，而是三份完整前端构建挂同域不同路径：
 *   canary     → /
 *   canary2    → /t/canary2/
 *   canary-pro → /t/canary-pro/
 *
 * 这个脚本只由 `zvp5/deployment/build-theme-apps.sh` 注入到两个子应用的
 * index.html（根 canary 不注入，它有自己的 /me 主题下拉），作用是让用户
 * 在子应用里也能一键跳回其它应用。因为同源，localStorage 里的 token 共享，
 * 跳转后仍是登录态。
 *
 * 依赖：
 *   - <meta name="zvms-api-base" content="..."> 由构建脚本注入，用来同步账号偏好；
 *   - localStorage['token'] 登录态；localStorage['zvms.theme'] 设备级主题缓存。
 *
 * 样式全部内联且不引用任何 --yg-* / --yg2-* / --pro-* 令牌，
 * 深浅色下都能看清，避免受各应用设计层影响。
 */
(function () {
  'use strict'

  if (window.__zvmsThemeSwitcherInstalled) {
    return
  }
  window.__zvmsThemeSwitcherInstalled = true

  var THEME_PATHS = {
    canary: '/',
    canary2: '/t/canary2/',
    'canary-pro': '/t/canary-pro/'
  }
  var THEME_LABELS = {
    canary: 'Canary',
    canary2: 'Canary 2',
    'canary-pro': 'Canary Pro'
  }
  var STORAGE_KEY = 'zvms.theme'

  function currentThemeId() {
    var path = window.location.pathname || '/'
    var ids = Object.keys(THEME_PATHS)
    for (var i = 0; i < ids.length; i += 1) {
      var id = ids[i]
      if (id === 'canary') continue
      var base = THEME_PATHS[id]
      if (path === base || path.indexOf(base) === 0) {
        return id
      }
    }
    return 'canary'
  }

  function apiBase() {
    var meta = document.querySelector('meta[name="zvms-api-base"]')
    var value = meta && meta.getAttribute('content')
    return (value || '').replace(/\/+$/, '')
  }

  function syncPreference(themeId) {
    try {
      window.localStorage.setItem(STORAGE_KEY, themeId)
    } catch (e) {
      // 隐私模式/配额满：跳转照做，只是下次打开会回到默认
    }
    var token = null
    try {
      token = window.localStorage.getItem('token')
    } catch (e) {
      token = null
    }
    var base = apiBase()
    if (!token || !base || typeof window.fetch !== 'function') {
      return
    }
    window
      .fetch(base + '/v1/themes/me', {
        method: 'PUT',
        headers: {
          Authorization: 'Bearer ' + token,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ theme: themeId })
      })
      .catch(function () {
        // 账号侧没记住不影响本次跳转，目标应用读的是同一份 localStorage
      })
  }

  function switchTo(themeId) {
    var target = THEME_PATHS[themeId]
    if (!target) {
      return
    }
    syncPreference(themeId)
    if (target !== window.location.pathname && currentThemeId() !== themeId) {
      window.location.assign(target)
    } else if (currentThemeId() === themeId) {
      close()
    }
  }

  var panel = null
  var trigger = null

  function close() {
    if (!panel || !trigger) return
    panel.hidden = true
    trigger.setAttribute('aria-expanded', 'false')
  }

  function open() {
    if (!panel || !trigger) return
    panel.hidden = false
    trigger.setAttribute('aria-expanded', 'true')
  }

  function build() {
    var host = document.createElement('div')
    host.id = 'zvms-theme-switcher'
    // 内联样式：不依赖任何应用的令牌，避免被各设计层覆盖
    host.setAttribute(
      'style',
      [
        'position:fixed',
        'right:12px',
        'bottom:12px',
        'z-index:2147483000',
        'font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif',
        'font-size:13px',
        'line-height:1.4',
        'color-scheme:light dark'
      ].join(';')
    )

    panel = document.createElement('div')
    panel.setAttribute('role', 'menu')
    panel.setAttribute('aria-label', '切换主题应用')
    panel.hidden = true
    panel.setAttribute(
      'style',
      [
        'position:absolute',
        'right:0',
        'bottom:calc(100% + 8px)',
        'min-width:9.5rem',
        'padding:4px',
        'border-radius:10px',
        'border:1px solid rgba(128,128,128,0.35)',
        'background:Canvas',
        'color:CanvasText',
        'box-shadow:0 8px 24px rgba(0,0,0,0.28)',
        'overflow:hidden'
      ].join(';')
    )

    var active = currentThemeId()
    Object.keys(THEME_PATHS).forEach(function (id) {
      var item = document.createElement('button')
      item.type = 'button'
      item.setAttribute('role', 'menuitemradio')
      item.setAttribute('aria-checked', id === active ? 'true' : 'false')
      item.textContent = THEME_LABELS[id] || id
      item.setAttribute(
        'style',
        [
          'display:block',
          'width:100%',
          'padding:8px 10px',
          'border:0',
          'border-radius:6px',
          'background:transparent',
          'color:inherit',
          'font:inherit',
          'text-align:left',
          'cursor:pointer',
          id === active ? 'font-weight:600' : 'font-weight:400'
        ].join(';')
      )
      if (id === active) {
        item.setAttribute('aria-current', 'true')
      }
      item.addEventListener('mouseenter', function () {
        item.style.background = 'rgba(128,128,128,0.18)'
      })
      item.addEventListener('mouseleave', function () {
        item.style.background = 'transparent'
      })
      item.addEventListener('click', function () {
        switchTo(id)
      })
      panel.appendChild(item)
    })

    trigger = document.createElement('button')
    trigger.type = 'button'
    trigger.textContent = '主题'
    trigger.setAttribute('aria-label', '切换主题应用（当前：' + (THEME_LABELS[active] || active) + '）')
    trigger.setAttribute('aria-haspopup', 'menu')
    trigger.setAttribute('aria-expanded', 'false')
    trigger.setAttribute(
      'style',
      [
        'padding:7px 12px',
        'border-radius:999px',
        'border:1px solid rgba(128,128,128,0.45)',
        'background:Canvas',
        'color:CanvasText',
        'font:inherit',
        'cursor:pointer',
        'box-shadow:0 2px 10px rgba(0,0,0,0.22)'
      ].join(';')
    )
    trigger.addEventListener('click', function () {
      if (panel.hidden) {
        open()
      } else {
        close()
      }
    })

    document.addEventListener('click', function (event) {
      if (!host.contains(event.target)) {
        close()
      }
    })
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        close()
      }
    })

    host.appendChild(panel)
    host.appendChild(trigger)
    document.body.appendChild(host)
  }

  if (document.body) {
    build()
  } else {
    document.addEventListener('DOMContentLoaded', build, { once: true })
  }
})()

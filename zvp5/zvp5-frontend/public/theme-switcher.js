/* 全局主题切换器（整站多构建版）。
 *
 * 主题不是「同一套页面换外壳」，而是多份完整前端构建挂同域不同路径：
 *   默认主题（canary）→ /
 *   其它主题 <id>     → /themes/<id>/
 *
 * 菜单完全由构建产物里的运行清单 `/themes.json` 驱动（由
 * `zvp5/deployment/build-theme-apps.mjs` 按 `zvp5/themes/manifest.json` 生成），
 * 这里不写死任何主题 id / 路径。新增主题只需改清单。
 *
 * 这个脚本只被注入到子应用的 index.html（根 canary 不注入，它有自己的 /me
 * 主题下拉），作用是让用户在子应用里也能一键跳回其它应用。因为同源，
 * localStorage 里的 token 共享，跳转后仍是登录态。
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

  var STORAGE_KEY = 'zvms.theme'
  var RUNTIME_MANIFEST_URL = '/themes.json'
  var THEME_PREFIX = '/themes/'
  var THEME_ID_RE = /^[a-z0-9][a-z0-9-]*$/
  // 清单读不到（本地 dev、构建脚本没跑）时至少还能跳回根应用。
  var FALLBACK_THEMES = [{ id: 'canary', label: 'Canary', base: '/' }]

  /** 该 id 对应的站内路径：canary → /，其它 → /themes/<id>/。 */
  function baseFor(id) {
    return id === 'canary' ? '/' : THEME_PREFIX + id + '/'
  }

  function currentThemeId() {
    var match = /^\/themes\/([a-z0-9][a-z0-9-]*)(?:\/|$)/.exec(window.location.pathname || '/')
    return match ? match[1] : 'canary'
  }

  /** 只接受合法 slug 与站内绝对路径，避免清单被污染时跳出站点。 */
  function normalizeTheme(item) {
    if (!item || typeof item.id !== 'string' || !THEME_ID_RE.test(item.id)) {
      return null
    }
    var base = typeof item.base === 'string' && item.base.charAt(0) === '/' ? item.base : baseFor(item.id)
    if (base.charAt(base.length - 1) !== '/') {
      base += '/'
    }
    return {
      id: item.id,
      label: typeof item.label === 'string' && item.label ? item.label : item.id,
      base: base
    }
  }

  function loadThemes(callback) {
    if (typeof window.fetch !== 'function') {
      callback(FALLBACK_THEMES)
      return
    }
    window
      .fetch(RUNTIME_MANIFEST_URL, { headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('manifest ' + response.status)
        }
        return response.json()
      })
      .then(function (manifest) {
        var list = manifest && manifest.themes
        if (!Array.isArray(list)) {
          throw new Error('manifest has no themes')
        }
        var themes = list
          .filter(function (item) {
            return !item || item.status !== 'deprecated'
          })
          .map(normalizeTheme)
          .filter(Boolean)
        callback(themes.length ? themes : FALLBACK_THEMES)
      })
      .catch(function () {
        callback(FALLBACK_THEMES)
      })
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

  function switchTo(theme) {
    syncPreference(theme.id)
    if (currentThemeId() === theme.id) {
      close()
      return
    }
    window.location.assign(theme.base)
  }

  var themes = FALLBACK_THEMES
  var panel = null
  var trigger = null

  function activeLabel() {
    var active = currentThemeId()
    for (var i = 0; i < themes.length; i += 1) {
      if (themes[i].id === active) {
        return themes[i].label
      }
    }
    return active
  }

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
    themes.forEach(function (theme) {
      var item = document.createElement('button')
      item.type = 'button'
      item.setAttribute('role', 'menuitemradio')
      item.setAttribute('aria-checked', theme.id === active ? 'true' : 'false')
      item.textContent = theme.label
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
          theme.id === active ? 'font-weight:600' : 'font-weight:400'
        ].join(';')
      )
      if (theme.id === active) {
        item.setAttribute('aria-current', 'true')
      }
      item.addEventListener('mouseenter', function () {
        item.style.background = 'rgba(128,128,128,0.18)'
      })
      item.addEventListener('mouseleave', function () {
        item.style.background = 'transparent'
      })
      item.addEventListener('click', function () {
        switchTo(theme)
      })
      panel.appendChild(item)
    })

    trigger = document.createElement('button')
    trigger.type = 'button'
    trigger.textContent = '主题'
    trigger.setAttribute('aria-label', '切换主题应用（当前：' + activeLabel() + '）')
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

  function start() {
    loadThemes(function (loaded) {
      themes = loaded
      build()
    })
  }

  if (document.body) {
    start()
  } else {
    document.addEventListener('DOMContentLoaded', start, { once: true })
  }
})()

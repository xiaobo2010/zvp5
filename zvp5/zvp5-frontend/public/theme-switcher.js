// 同源部署的主题目录、切换和按版本记录的首次引导。各应用自行渲染入口按钮。
// 不依赖原生 <dialog>/showModal：学海平板浏览器可能没有这套 API。
;(function () {
  const meta = document.querySelector('meta[name="zvms-api-base"]')
  if (!meta) return

  const base = meta.content.replace(/\/+$/, '')
  const active = /^\/themes\/([a-z0-9-]+)(?:\/|$)/.exec(location.pathname)?.[1] || 'canary'
  const guideKey = `zvms.theme-guide.v1.${active}`
  const english = document.documentElement.lang.toLowerCase().startsWith('en')
  const text = english
    ? { title: 'Choose a theme', guide: 'Pick a layout to get started. You can change it from your profile at any time.', dismiss: 'Not now', current: 'Current', loading: 'Loading themes…', error: 'Themes could not be loaded. Close and try again.', saving: 'Saving…', local: 'Saved on this device.' }
    : { title: '选择站点主题', guide: '选一套你喜欢的界面。以后可以在个人主页随时切换。', dismiss: '稍后再选', current: '当前主题', loading: '正在加载主题…', error: '主题加载失败，请关闭后重试。', saving: '正在保存…', local: '已保存在此设备。' }

  function read(key) {
    try { return localStorage.getItem(key) } catch { return null }
  }

  function write(key, value) {
    try { localStorage.setItem(key, value) } catch { /* 隐私模式仍可切换 */ }
  }

  function element(tag, className, content) {
    const node = document.createElement(tag)
    node.className = className
    if (content) node.textContent = content
    return node
  }

  const overlay = element('div', 'zvms-theme-overlay')
  overlay.hidden = true
  overlay.setAttribute('role', 'presentation')

  const dialog = element('div', 'zvms-theme-dialog')
  dialog.dataset.themeApp = active
  dialog.setAttribute('role', 'dialog')
  dialog.setAttribute('aria-modal', 'true')
  dialog.setAttribute('aria-labelledby', 'zvms-theme-title')

  const title = element('h2', 'zvms-theme-dialog__title', text.title)
  title.id = 'zvms-theme-title'
  title.tabIndex = -1
  const guide = element('p', 'zvms-theme-dialog__guide', text.guide)
  const list = element('div', 'zvms-theme-dialog__list')
  const status = element('p', 'zvms-theme-dialog__status')
  status.setAttribute('role', 'status')
  const dismiss = element('button', 'zvms-theme-dialog__dismiss', text.dismiss)
  dismiss.type = 'button'
  dialog.append(title, guide, list, status, dismiss)
  overlay.append(dialog)
  document.body.append(overlay)

  let choices
  let saving = false
  let firstVisit = false
  let opener = null
  let previousOverflow = ''

  async function loadThemes() {
    if (choices) return choices
    const token = read('token')
    if (token) {
      try {
        // 主题目录的权威来源是后端注册表；后端同时给出每个主题的页面入口
        // ``path``，前端不再自己拼 ``/themes/<id>/``。
        let endpoint = `${base}/v1/themes`
        try {
          if (new URL(base).origin !== location.origin) {
            endpoint = `${location.origin}/api/v1/themes`
          }
        } catch { /* meta 不是绝对地址时只走它本身 */ }
        const response = await fetch(endpoint, {
          headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
          cache: 'no-store'
        })
        if (response.ok) {
          const payload = await response.json()
          const items = Array.isArray(payload?.data?.items) ? payload.data.items : []
          const catalog = items.filter((item) => {
            if (!item || typeof item.id !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(item.id)) return false
            if (typeof item.label !== 'string' || !item.label) return false
            return typeof item.path === 'string' && /^\/(?:themes\/[a-z0-9][a-z0-9-]*\/)?$/.test(item.path)
          }).map((item) => ({
            id: item.id,
            label: item.label,
            description: typeof item.description === 'string' ? item.description : '',
            status: item.status,
            base: item.path
          }))
          if (catalog.length) {
            choices = catalog
            return choices
          }
        }
      } catch { /* 后端不可用时回落静态清单 */ }
    }
    const response = await fetch('/themes.json', {
      headers: { Accept: 'application/json' },
      cache: 'no-store'
    })
    if (!response.ok) throw new Error('Theme catalog unavailable')
    const manifest = await response.json()
    choices = manifest.themes.filter((item) => {
      if (!item || typeof item.id !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(item.id) || typeof item.label !== 'string') return false
      const expected = item.id === manifest.default ? '/' : `/themes/${item.id}/`
      return item.status !== 'deprecated' && item.base === expected
    })
    if (!choices.length) {
      choices = undefined
      throw new Error('Empty theme catalog')
    }
    return choices
  }

  function requestTheme(endpoint, id, token) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 1500)
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    try {
      const xuehai = Number(window.xhBrowserJava?.getUserId?.())
      if (Number.isFinite(xuehai) && xuehai > 0) headers['Xuehai-Id'] = String(xuehai)
    } catch { /* 没有学海桥时不带头 */ }
    return fetch(endpoint, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ theme: id }),
      // 页面随即整页跳转；keepalive 让这次偏好写入在卸载后继续完成，
      // 否则切主题后刷新/换设备会读回旧偏好。
      keepalive: true,
      signal: controller.signal
    }).then((response) => response.ok).catch(() => false).finally(() => clearTimeout(timeout))
  }

  async function save(id) {
    const token = read('token')
    if (!token) return true
    // 生产页面源和静态 API 源可能不同。先走同源 /api，避免平板上跨域预检失败。
    let sameOrigin = ''
    try {
      if (new URL(base).origin !== location.origin) {
        sameOrigin = `${location.origin}/api/v1/themes/me`
      }
    } catch { /* meta 不是绝对地址时只走它本身 */ }
    if (sameOrigin && await requestTheme(sameOrigin, id, token)) return true
    return requestTheme(`${base}/v1/themes/me`, id, token)
  }

  function clearQuery() {
    const url = new URL(location.href)
    if (!url.searchParams.has('choose-theme')) return
    url.searchParams.delete('choose-theme')
    history.replaceState(history.state, '', url)
  }

  function close() {
    if (overlay.hidden) return
    overlay.hidden = true
    document.body.style.overflow = previousOverflow
    if (firstVisit) write(guideKey, 'done')
    clearQuery()
    if (opener && opener.focus) opener.focus()
  }

  async function select(theme) {
    if (saving) return
    saving = true
    write(guideKey, 'done')
    write('zvms.theme', theme.id)
    // 先切换页面，再尽力同步账号：平板跨域失败也不该卡住整站切换。
    if (theme.id !== active) {
      void save(theme.id)
      location.assign(theme.base)
      return
    }
    status.textContent = text.saving
    list.querySelectorAll('button').forEach((button) => { button.disabled = true })
    dismiss.disabled = true
    const saved = await save(theme.id)
    saving = false
    dismiss.disabled = false
    if (saved) {
      close()
    } else {
      list.querySelectorAll('button').forEach((button) => { button.disabled = false })
      status.textContent = text.local
      dismiss.focus()
    }
  }

  async function open(showGuide, trigger) {
    if (!overlay.hidden || saving) return
    opener = trigger
    firstVisit = showGuide
    guide.hidden = !firstVisit
    list.replaceChildren()
    status.textContent = text.loading
    const shell = active === 'canary' ? document.querySelector('.yg-phi') : null
    const container = shell || document.body
    container.append(overlay)
    overlay.hidden = false
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    title.focus()
    try {
      const themes = await loadThemes()
      if (overlay.hidden) return
      list.replaceChildren(...themes.map((theme) => {
        const button = element('button', 'zvms-theme-dialog__option')
        button.type = 'button'
        button.append(element('strong', '', theme.label))
        if (theme.id === active) {
          button.append(element('span', 'zvms-theme-dialog__current', text.current))
        }
        if (!english && typeof theme.description === 'string') {
          button.append(element('span', 'zvms-theme-dialog__description', theme.description))
        }
        button.addEventListener('click', () => { void select(theme) })
        return button
      }))
      status.textContent = ''
    } catch {
      status.textContent = text.error
    }
  }

  dismiss.addEventListener('click', close)
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) close()
  })
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !overlay.hidden) close()
  })
  document.addEventListener('click', (event) => {
    const trigger = event.target instanceof Element && event.target.closest('[data-zvms-theme-picker]')
    if (!trigger || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
    event.preventDefault()
    void open(false, trigger)
  })

  const url = new URL(location.href)
  if (url.searchParams.has('choose-theme')) {
    void open(!read(guideKey))
  } else if (!read(guideKey)) {
    // 等候首屏完成，目录缺失时保持静默，不打断本地开发或独立部署。
    const ready = () => {
      setTimeout(() => {
        void loadThemes().then((themes) => {
          if (themes.length > 1 && !read(guideKey)) void open(true)
        }).catch(() => {})
      }, 650)
    }
    if (document.readyState === 'complete') ready()
    else window.addEventListener('load', ready, { once: true })
  }
})()

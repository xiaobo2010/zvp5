// 同源部署的主题目录、切换和按版本记录的首次引导。各应用自行渲染入口按钮。
;(function () {
  const meta = document.querySelector('meta[name="zvms-api-base"]')
  if (!meta || typeof HTMLDialogElement === 'undefined' || !HTMLDialogElement.prototype.showModal) return

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

  function element(tag, className, content = '') {
    const node = document.createElement(tag)
    node.className = className
    node.textContent = content
    return node
  }

  const dialog = element('dialog', 'zvms-theme-dialog')
  dialog.dataset.themeApp = active
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
  document.body.append(dialog)

  let choices
  let saving = false
  let firstVisit = false
  let opener = null

  async function loadThemes() {
    if (choices) return choices
    const response = await fetch('/themes.json', { headers: { Accept: 'application/json' } })
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

  async function save(id) {
    const token = read('token')
    if (!token) return true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 1800)
    try {
      const response = await fetch(`${base}/v1/themes/me`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: id }),
        signal: controller.signal
      })
      return response.ok
    } catch {
      return false
    } finally {
      clearTimeout(timeout)
    }
  }

  async function select(theme) {
    if (saving) return
    saving = true
    write(guideKey, 'done')
    write('zvms.theme', theme.id)
    status.textContent = text.saving
    list.querySelectorAll('button').forEach((button) => { button.disabled = true })
    dismiss.disabled = true
    const saved = await save(theme.id)
    if (theme.id !== active) {
      location.assign(theme.base)
      return
    }
    saving = false
    dismiss.disabled = false
    if (saved) {
      dialog.close()
    } else {
      list.querySelectorAll('button').forEach((button) => { button.disabled = false })
      status.textContent = text.local
      dismiss.focus()
    }
  }

  async function open(showGuide, trigger = null) {
    if (dialog.open || saving) return
    opener = trigger
    firstVisit = showGuide
    guide.hidden = !firstVisit
    list.replaceChildren()
    status.textContent = text.loading
    const shell = active === 'canary' ? document.querySelector('.yg-phi') : null
    const container = shell || document.body
    container.append(dialog)
    dialog.showModal()
    title.focus()
    try {
      const themes = await loadThemes()
      if (!dialog.open) return
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

  dismiss.addEventListener('click', () => dialog.close())
  dialog.addEventListener('cancel', (event) => {
    if (saving) event.preventDefault()
  })
  dialog.addEventListener('close', () => {
    if (firstVisit) write(guideKey, 'done')
    const url = new URL(location.href)
    if (url.searchParams.has('choose-theme')) {
      url.searchParams.delete('choose-theme')
      history.replaceState(history.state, '', url)
    }
    opener?.focus()
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

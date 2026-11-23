import { API_BASE } from '@/api/http'

const THEME_ID = /^[a-z0-9][a-z0-9-]{0,31}$/
const THEME_PREFIX = '/themes/'
const PRO_THEME_ID = 'canary-pro'
const CANARY_ORIGIN = 'https://canary.xiaobopisces.top'
export const SITE_THEME_STORAGE_KEY = 'zvms.theme'

export interface SiteTheme {
  id: string
  label: string
  description: string
  status: 'stable' | 'experimental'
  base: string
}

// Snapshot of the published Canary manifest for standalone Pro builds.
const standaloneThemes: SiteTheme[] = [
  {
    id: 'canary', label: 'Canary', status: 'stable', base: '/',
    description: '经典 Phi 设计：暗色棱角、青色判定线与现有导航',
  },
  {
    id: PRO_THEME_ID, label: 'Canary Pro', status: 'stable', base: '/themes/canary-pro/',
    description: 'Pro 工作台：独立左侧导航、液态玻璃卡片与工作台布局',
  },
  {
    id: 'canary-anthropic', label: 'Canary Anthropic', status: 'experimental',
    base: '/themes/canary-anthropic/',
    description: '暖色工作台：柔和中性色、圆角面板与自适应导航',
  },
]

function isThemeId(value: unknown): value is string {
  return typeof value === 'string' && THEME_ID.test(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function loadBackendThemes(signal: AbortSignal): Promise<SiteTheme[] | null> {
  const token = readToken()
  if (!token) return null
  try {
    const response = await fetch(`${API_BASE}/v1/themes`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      signal,
    })
    if (!response.ok) return null
    const payload: unknown = await response.json()
    if (!isRecord(payload) || payload.status !== 'ok' || !isRecord(payload.data)) return null
    const items = payload.data.items
    if (!Array.isArray(items)) return null
    const themes: SiteTheme[] = []
    for (const item of items) {
      if (!isRecord(item) || !isThemeId(item.id) || typeof item.label !== 'string' ||
          (item.status !== 'stable' && item.status !== 'experimental') ||
          typeof item.path !== 'string' ||
          !/^\/(?:themes\/[a-z0-9][a-z0-9-]*\/)?$/.test(item.path)) continue
      themes.push({
        id: item.id,
        label: item.label,
        description: typeof item.description === 'string' ? item.description : '',
        status: item.status,
        base: item.path,
      })
    }
    return themes.length > 1 ? themes : null
  } catch {
    // 后端不可达时退回构建产物里的静态清单。
    return null
  }
}

export async function loadSiteThemes(signal: AbortSignal): Promise<SiteTheme[]> {
  if (switchesSiteOrigin()) return standaloneThemes

  // 主题目录以后端注册表为准：新增主题（例如 canary-anthropic）只要后端
  // 注册表有，Pro 的选择器就会出现，不需要等 Pro 再发一版快照。
  const backend = await loadBackendThemes(signal)
  if (backend) return backend

  // This file is emitted at the host root, including when Pro runs at a Vite subpath.
  const response = await fetch('/themes.json', {
    headers: { Accept: 'application/json' },
    credentials: 'omit',
    redirect: 'error',
    signal,
  })
  if (!response.ok) throw new Error('Theme catalog unavailable')

  const manifest: unknown = await response.json()
  if (!isRecord(manifest) || !isThemeId(manifest.default) ||
      manifest.prefix !== THEME_PREFIX || !Array.isArray(manifest.themes)) {
    throw new Error('Invalid theme catalog')
  }

  const themes: SiteTheme[] = []
  const ids = new Set<string>()
  const items: unknown[] = manifest.themes
  for (const item of items) {
    if (!isRecord(item) || !isThemeId(item.id) || ids.has(item.id) ||
        typeof item.label !== 'string' || !item.label.trim() ||
        (item.status !== 'stable' && item.status !== 'experimental')) continue

    const expectedBase = item.id === manifest.default ? '/' : `${THEME_PREFIX}${item.id}/`
    if (item.base !== expectedBase) continue

    ids.add(item.id)
    themes.push({
      id: item.id,
      label: item.label,
      description: typeof item.description === 'string' ? item.description : '',
      status: item.status,
      base: item.base,
    })
  }

  if (!ids.has(manifest.default) || !ids.has(PRO_THEME_ID)) throw new Error('Incomplete theme catalog')
  return themes
}

export function currentSiteTheme(themes: SiteTheme[]): string {
  const base = import.meta.env.BASE_URL
  // A standalone Pro build also uses '/', which belongs to Canary on the shared host.
  return themes.find((theme) => theme.base !== '/' && theme.base === base)?.id ?? PRO_THEME_ID
}

export function siteThemeDestination(theme: SiteTheme): string {
  const origin = switchesSiteOrigin() ? CANARY_ORIGIN : window.location.origin
  const destination = new URL(theme.base, origin)
  return destination.href
}

export function switchesSiteOrigin(): boolean {
  return import.meta.env.BASE_URL === '/' && window.location.origin !== CANARY_ORIGIN
}

export function readSiteTheme(): string | null {
  try {
    const id = localStorage.getItem(SITE_THEME_STORAGE_KEY)
    return isThemeId(id) ? id : null
  } catch {
    return null
  }
}

function readToken(): string | null {
  try {
    return localStorage.getItem('token')
  } catch {
    return null
  }
}

async function requestAccountTheme(token: string, controller: AbortController, id?: string): Promise<string | null> {
  const timeout = window.setTimeout(() => controller.abort(), 1800)
  const headers = new Headers({ Accept: 'application/json', Authorization: `Bearer ${token}` })
  if (id) headers.set('Content-Type', 'application/json')

  try {
    const response = await fetch(`${API_BASE}/v1/themes/me`, {
      method: id ? 'PUT' : 'GET',
      headers,
      body: id ? JSON.stringify({ theme: id }) : undefined,
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      signal: controller.signal,
    })
    if (!response.ok) return null

    const payload: unknown = await response.json()
    if (!isRecord(payload) || payload.status !== 'ok' || !isRecord(payload.data)) return null
    return isThemeId(payload.data.theme) ? payload.data.theme : null
  } catch {
    // Preference sync is optional and does not affect the user's session.
    return null
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function loadAccountTheme(controller: AbortController): Promise<string | null> {
  const token = readToken()
  return token ? requestAccountTheme(token, controller) : null
}

export async function saveSiteTheme(
  id: string,
): Promise<{ local: boolean; account: 'saved' | 'failed' | 'skipped' }> {
  let local = false
  try {
    localStorage.setItem(SITE_THEME_STORAGE_KEY, id)
    local = true
  } catch { /* Theme switching also works with storage blocked. */ }

  const token = readToken()
  if (!token) return { local, account: 'skipped' }

  const saved = await requestAccountTheme(token, new AbortController(), id)
  return { local, account: saved === id ? 'saved' : 'failed' }
}

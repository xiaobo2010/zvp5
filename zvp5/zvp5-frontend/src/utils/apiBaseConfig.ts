function validateAbsoluteApiBase(value: string | undefined, envName: string): string {
  const apiBase = (value || '').trim()
  if (!apiBase) {
    throw new Error(
      `${envName} is required for production builds and must be an absolute HTTPS URL ending in /api`
    )
  }

  let parsed: URL
  try {
    parsed = new URL(apiBase)
  } catch {
    throw new Error(`${envName} must be an absolute URL`)
  }
  if (
    parsed.protocol !== 'https:' ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname.replace(/\/+$/, '') !== '/api'
  ) {
    throw new Error(
      `${envName} must be an absolute HTTPS URL ending in /api with no credentials, query, or fragment`
    )
  }
  return apiBase
}

export function validateProductionApiBase(value: string | undefined): string {
  // Same-origin is the canonical deployment: the final page Origin is zvp and
  // /api is served by that same vhost. Keeping this relative also keeps test
  // deployments origin-agnostic until the manual DNS cutover.
  if ((value || '').trim() === '/api') return '/api'
  return validateAbsoluteApiBase(value, 'VITE_API_BASE')
}

/**
 * 首尔竞速线路是可选配置：未配置时所有请求仍走 VITE_API_BASE；一旦配置，
 * 就必须和主基址遵守同一套 HTTPS 约束。
 */
export function validateOptionalProductionRaceApiBase(
  value: string | undefined
): string | undefined {
  return (value || '').trim() ? validateAbsoluteApiBase(value, 'VITE_API_RACE_BASE') : undefined
}

/**
 * 内容主机基址（`/system/files/*` 这类在「发页面的那台机器」上执行的端点）。
 *
 * 未配置时按页面源推导，生产与本地开发行为不变。Cloudflare 预览站只发静态资产，
 * 页面源的 `/api` 会被 SPA fallback 接走（200 + index.html），必须显式指到 API 域。
 */
export function resolveContentApiBase(
  configured: string | undefined,
  pageOrigin?: string
): string {
  const explicit = (configured || '').trim()
  if (explicit) {
    return explicit.replace(/\/+$/, '')
  }
  return pageOrigin ? `${pageOrigin}/api` : '/api'
}

export function validateOptionalContentApiBase(value: string | undefined): string | undefined {
  return (value || '').trim()
    ? validateAbsoluteApiBase(value, 'VITE_API_CONTENT_BASE')
    : undefined
}

function validateHttpsOrRootBase(value: string | undefined, envName: string): string {
  const raw = (value || '').trim()
  if (!raw || raw === '/') return '/'

  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    throw new Error(`${envName} must be / or an absolute HTTPS URL`)
  }

  if (
    parsed.protocol !== 'https:' ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error(`${envName} must be / or an absolute HTTPS URL`)
  }

  return parsed.href.endsWith('/') ? parsed.href : `${parsed.href}/`
}

/**
 * 静态资源基址。生产环境可指向独立的高带宽静态域；未配置时保持同源 `/`。
 *
 * Vite 会把该值同时用于 HTML、动态 import 和 public 资源。这里只接受根路径
 * 或绝对 HTTPS 地址，避免把构建产物写成无法跨域加载的协议相对 URL。
 */
export function validateStaticBase(value: string | undefined): string {
  return validateHttpsOrRootBase(value, 'VITE_STATIC_BASE')
}

/** 页面壳 Origin，用于跨域 Manifest 仍把 PWA scope/start_url 锚定在页面域。 */
export function validatePageBase(value: string | undefined): string {
  return validateHttpsOrRootBase(value, 'VITE_PAGE_BASE')
}

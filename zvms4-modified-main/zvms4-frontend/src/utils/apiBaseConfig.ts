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
  return validateAbsoluteApiBase(value, 'VITE_API_BASE')
}

/**
 * 后端竞速线路是可选配置：未配置时所有请求仍走 VITE_API_BASE；一旦配置，
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

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
 * 首尔竞速线路是可选配置：未配置时所有请求仍走 VITE_API_BASE；一旦配置，
 * 就必须和主基址遵守同一套 HTTPS 约束。
 */
export function validateOptionalProductionRaceApiBase(
  value: string | undefined
): string | undefined {
  return (value || '').trim() ? validateAbsoluteApiBase(value, 'VITE_API_RACE_BASE') : undefined
}

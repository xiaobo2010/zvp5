export function validateProductionApiBase(value: string | undefined): string {
  const apiBase = (value || '').trim()
  if (!apiBase) {
    throw new Error(
      'VITE_API_BASE is required for production builds and must be an absolute HTTPS URL ending in /api'
    )
  }

  let parsed: URL
  try {
    parsed = new URL(apiBase)
  } catch {
    throw new Error('VITE_API_BASE must be an absolute URL')
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
      'VITE_API_BASE must be an absolute HTTPS URL ending in /api with no credentials, query, or fragment'
    )
  }
  return apiBase
}

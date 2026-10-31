/**
 * API ownership split used by the browser request layer.
 *
 * The database adjacent origin is the default.  These routes are different:
 * they operate on the web host's local certificate, filesystem, or SSH
 * socket, so sending them through the database host adds a network hop and
 * can address the wrong machine's local resources.
 */
const CONTENT_HOST_EXACT_PATHS = new Set([
  '/system/ssh/session',
  '/system/ssh/ws'
])

const CONTENT_HOST_PREFIXES = ['/system/files/']

export function normalizeApiPath(value: unknown): string {
  if (typeof value !== 'string') {
    return ''
  }

  const withoutQuery = value.split(/[?#]/, 1)[0]
  if (!withoutQuery) {
    return ''
  }

  const path = withoutQuery.startsWith('/') ? withoutQuery : `/${withoutQuery}`
  return path.replace(/^\/api(?=\/|$)/i, '') || '/'
}

export function usesContentHost(value: unknown): boolean {
  const path = normalizeApiPath(value)
  return (
    CONTENT_HOST_EXACT_PATHS.has(path) ||
    CONTENT_HOST_PREFIXES.some((prefix) => path.startsWith(prefix))
  )
}

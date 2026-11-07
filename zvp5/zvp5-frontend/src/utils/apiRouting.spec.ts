import { describe, expect, it } from 'vitest'
import { normalizeApiPath, usesContentHost } from './apiRouting'

describe('API host routing', () => {
  it('normalizes relative and /api-prefixed paths', () => {
    expect(normalizeApiPath('/api/system/files/download')).toBe('/system/files/download')
    expect(normalizeApiPath('system/ssh/ws')).toBe('/system/ssh/ws')
  })

  it.each(['/system/files/list', '/system/files/download'])(
    'keeps local endpoint %s on the content host',
    (path) => {
      expect(usesContentHost(path)).toBe(true)
    }
  )

  // SSH 会话与 WS 都由 API 服务器的网关后端发起,不再属于页面源本机资源。
  it.each([
    '/cert',
    '/health',
    '/version',
    '/system/ssh/session',
    '/system/ssh/ws',
    '/system/ssh/targets',
    '/system/ssh/suggest',
    '/system/db/metrics/host',
    '/users/auth'
  ])('keeps database or API endpoint %s on the API host', (path) => {
    expect(usesContentHost(path)).toBe(false)
  })
})

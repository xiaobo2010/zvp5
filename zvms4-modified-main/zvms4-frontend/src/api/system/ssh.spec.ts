import { beforeEach, expect, it, vi } from 'vitest'
import { buildSshWsUrl, buildSshWsUrlFor, createSshSession, listSshTargets } from './ssh'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

// baseURL 指向 API host,contentBaseURL 指向页面源:WS 必须选中前者。
vi.mock('@/plugins/axios', () => ({
  default: { get, post },
  baseURL: 'https://api.example.test/api',
  contentBaseURL: 'https://content.example.test/api'
}))

beforeEach(() => {
  get.mockReset()
  post.mockReset()
})

it('keeps a relative API base on the content origin', () => {
  expect(buildSshWsUrlFor('/api', 'one-time', 'https://content.example.test')).toBe(
    'wss://content.example.test/api/system/ssh/ws?ticket=one-time'
  )
})

it('uses the absolute API origin instead of the content origin', () => {
  expect(
    buildSshWsUrlFor('https://api.example.test/api', 'one-time', 'https://content.example.test/app')
  ).toBe('wss://api.example.test/api/system/ssh/ws?ticket=one-time')
})

it('builds the websocket url from the API base, not the content base', () => {
  expect(buildSshWsUrl('one-time')).toBe('wss://api.example.test/api/system/ssh/ws?ticket=one-time')
})

it('reads the target catalog from the API host', async () => {
  get.mockResolvedValueOnce({
    data: { status: 'ok', code: 200, data: { targets: [], default: '' } }
  })

  await listSshTargets()

  expect(get).toHaveBeenCalledWith('/system/ssh/targets')
})

it('exchanges the totp code together with the chosen target', async () => {
  post.mockResolvedValueOnce({
    data: { status: 'ok', code: 200, data: { ticket: 't', expires_in: 120 } }
  })

  await createSshSession('123456', 'api')

  expect(post).toHaveBeenCalledWith('/system/ssh/session', {
    totp_code: '123456',
    target: 'api'
  })
})

it('omits the target field when none was chosen', async () => {
  post.mockResolvedValueOnce({
    data: { status: 'ok', code: 200, data: { ticket: 't', expires_in: 120 } }
  })

  await createSshSession('123456')

  expect(post).toHaveBeenCalledWith('/system/ssh/session', { totp_code: '123456' })
})

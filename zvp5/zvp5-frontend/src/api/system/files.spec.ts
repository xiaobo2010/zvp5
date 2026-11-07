import { beforeEach, describe, expect, it, vi } from 'vitest'
import { deleteEntry, editFile, makeDir, renamePath } from './files'

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }))

vi.mock('@/plugins/axios', () => ({ default: { post, get } }))

const request = post

beforeEach(() => {
  request.mockReset()
})

describe('files API contract', () => {
  it('writes text back with path/content/encoding and no totp code', async () => {
    request.mockResolvedValueOnce({
      data: {
        status: 'ok',
        code: 200,
        data: { path: '/etc/app.conf', size: 12, encoding: 'utf-8' }
      }
    })

    await editFile('/etc/app.conf', 'hello world\n', 'utf-8')
    expect(request).toHaveBeenCalledWith('/system/files/edit', {
      path: '/etc/app.conf',
      content: 'hello world\n',
      encoding: 'utf-8'
    })
  })

  it('deletes without a totp code', async () => {
    request.mockResolvedValueOnce({ data: { status: 'ok', code: 200, data: {} } })

    await deleteEntry('/tmp/a.log', true)
    expect(request).toHaveBeenCalledWith('/system/files/delete', {
      path: '/tmp/a.log',
      recursive: true
    })
  })

  it('keeps the dual-auth code on mkdir and rename', async () => {
    request.mockResolvedValueOnce({ data: { status: 'ok', code: 200, data: {} } })
    request.mockResolvedValueOnce({ data: { status: 'ok', code: 200, data: {} } })

    await makeDir('/tmp/dir', '123456')
    await renamePath('/tmp/a.log', '/tmp/b.log', '654321')

    expect(request).toHaveBeenNthCalledWith(1, '/system/files/mkdir', {
      path: '/tmp/dir',
      totp_code: '123456'
    })
    expect(request).toHaveBeenNthCalledWith(2, '/system/files/rename', {
      from: '/tmp/a.log',
      to: '/tmp/b.log',
      totp_code: '654321'
    })
  })
})

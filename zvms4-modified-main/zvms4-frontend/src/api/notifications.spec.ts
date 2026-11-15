import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from '@/plugins/axios'
import {
  createNotification,
  deleteNotification,
  listActive,
  listAll,
  updateNotification
} from './notifications'

vi.mock('@/plugins/axios', () => ({ default: vi.fn() }))

const request = vi.mocked(axios)

const notice = {
  id: '0d00000000000000000000aa',
  title: '活动调整',
  content: '本周活动改到周六。',
  version: 2,
  author_id: '000000000000000000000001',
  author_name: '张老师',
  created_at: '2026-10-21T09:30:00',
  updated_at: '2026-10-21T10:15:00',
  active: true
}

beforeEach(() => {
  request.mockReset()
})

describe('notifications API contract', () => {
  it('reads the active list envelope and passes an abort signal', async () => {
    const signal = new AbortController().signal
    request.mockResolvedValueOnce({
      data: { status: 'ok', code: 200, data: { version: 7, notifications: [notice] } }
    })

    await expect(listActive(signal)).resolves.toEqual({ version: 7, notifications: [notice] })
    expect(request).toHaveBeenCalledWith({
      method: 'get',
      url: '/v1/notifications',
      signal
    })
  })

  it('uses the admin path for the full list', async () => {
    request.mockResolvedValueOnce({
      data: { status: 'ok', code: 200, data: { version: 7, notifications: [] } }
    })

    await listAll()
    expect(request).toHaveBeenCalledWith({
      method: 'get',
      url: '/v1/notifications/admin'
    })
  })

  it('creates with only title/content so the server resolves the author', async () => {
    request.mockResolvedValueOnce({
      data: {
        status: 'ok',
        code: 201,
        data: { notification: notice, version: 8 }
      }
    })

    await expect(
      createNotification({ title: notice.title, content: notice.content })
    ).resolves.toEqual({ notification: notice, version: 8 })
    expect(request).toHaveBeenCalledWith({
      method: 'post',
      url: '/v1/notifications',
      data: { title: notice.title, content: notice.content }
    })
  })

  it('sends partial updates and deletes by id', async () => {
    request.mockResolvedValueOnce({
      data: {
        status: 'ok',
        code: 200,
        data: { notification: { ...notice, active: false }, version: 9 }
      }
    })
    request.mockResolvedValueOnce({ status: 204, data: '' })

    await updateNotification(notice.id, { active: false })
    expect(request).toHaveBeenNthCalledWith(1, {
      method: 'put',
      url: `/v1/notifications/${notice.id}`,
      data: { active: false }
    })

    await deleteNotification(notice.id)
    expect(request).toHaveBeenNthCalledWith(2, {
      method: 'delete',
      url: `/v1/notifications/${notice.id}`
    })
  })

  it('throws an error envelope instead of returning undefined data', async () => {
    request.mockResolvedValueOnce({
      data: { status: 'error', code: 403, message: '权限不足' }
    })

    await expect(listAll()).rejects.toMatchObject({ code: 403 })
  })
})

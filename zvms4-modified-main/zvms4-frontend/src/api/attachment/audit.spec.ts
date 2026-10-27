import { beforeEach, describe, expect, it, vi } from 'vitest'
import { decideAttachment, getAttachmentAudit, listAuditAttachments } from './audit'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

/** axios 实例返回的是响应体，信封在 response.data 里。 */
function envelope(data: unknown, size: number) {
  return {
    status: 200,
    data: {
      status: 'ok',
      code: 200,
      data,
      metadata: { size }
    }
  }
}

function errorEnvelope(message: string, code = 403) {
  return {
    status: 200,
    data: {
      status: 'error',
      code,
      message
    }
  }
}

/** 附件 API 层沿用仓库惯例：axios(url, config) 位置参数。 */
function paramsOf(index = 0): Record<string, unknown> {
  return axiosMock.mock.calls[index]![1].params as Record<string, unknown>
}

beforeEach(() => {
  axiosMock.mockReset()
})

describe('附件管理 API 契约', () => {
  it('固定 scope=all 并带上第一页、每页 20 条', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAuditAttachments()

    expect(axiosMock).toHaveBeenCalledWith(
      '/attachments',
      expect.objectContaining({
        method: 'get',
        params: { scope: 'all', page: 1, size: 20 }
      })
    )
  })

  it('把筛选条件翻成后端约定的 query 键', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAuditAttachments({
      purpose: 'volunteer_order',
      status: 'verified',
      kind: 'original',
      ocrStatus: 'done',
      ownerOid: '  owner-1  ',
      createdFrom: '2026-10-01T00:00:00.000Z',
      createdTo: '2026-10-02T00:00:00.000Z'
    })

    expect(paramsOf()).toEqual({
      scope: 'all',
      page: 1,
      size: 20,
      purpose: 'volunteer_order',
      status: 'verified',
      kind: 'original',
      ocr_status: 'done',
      owner_oid: 'owner-1',
      created_from: '2026-10-01T00:00:00.000Z',
      created_to: '2026-10-02T00:00:00.000Z'
    })
  })

  it('空白筛选项不落到 query string', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAuditAttachments({ purpose: '   ', ownerOid: '', status: undefined })

    expect(paramsOf()).toEqual({ scope: 'all', page: 1, size: 20 })
  })

  it('页码与每页条数被夹到合法区间', async () => {
    axiosMock.mockResolvedValueOnce(envelope([], 0))

    await listAuditAttachments({ page: 0, size: 9999 })

    expect(paramsOf()).toEqual({ scope: 'all', page: 1, size: 100 })
  })

  it('列表返回 data 与 metadata.size', async () => {
    const row = { _id: 'oid-1', kind: 'original', mime: 'image/jpeg' }
    axiosMock.mockResolvedValueOnce(envelope([row], 7))

    const result = await listAuditAttachments()

    expect(result.data).toEqual([row])
    expect(result.total).toBe(7)
  })

  it('后端回 error 信封时抛错，把 message 带到调用方', async () => {
    axiosMock.mockResolvedValueOnce(errorEnvelope('Permission denied'))

    await expect(listAuditAttachments()).rejects.toThrow('Permission denied')
  })

  it('读审计详情走 /attachments/{oid}/audit', async () => {
    axiosMock.mockResolvedValueOnce(envelope({ _id: 'oid-1', ocr_text: 'hi' }, 0))

    const detail = await getAttachmentAudit('oid-1')

    expect(axiosMock).toHaveBeenCalledWith(
      '/attachments/oid-1/audit',
      expect.objectContaining({ method: 'get' })
    )
    expect(detail.ocr_text).toBe('hi')
  })

  it('裁决用 PATCH 提交 status 与 note', async () => {
    axiosMock.mockResolvedValueOnce(envelope(null, 0))

    await decideAttachment('oid-1', { status: 'verified', note: '材料齐全' })

    expect(axiosMock).toHaveBeenCalledWith(
      '/attachments/oid-1/status',
      expect.objectContaining({
        method: 'patch',
        data: { status: 'verified', note: '材料齐全' }
      })
    )
  })

  it('裁决失败时抛错', async () => {
    axiosMock.mockResolvedValueOnce(errorEnvelope('already decided', 409))

    await expect(decideAttachment('oid-1', { status: 'rejected' })).rejects.toThrow(
      'already decided'
    )
  })
})

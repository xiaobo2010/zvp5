import axios from '@/plugins/axios'
import type {
  Response,
  AttachmentMeta,
  AttachmentListItem
} from '@/../types'

export async function getMeta(oid: string): Promise<AttachmentMeta | undefined> {
  const result = (await axios(`/attachments/${oid}/meta`)).data as Response<AttachmentMeta>
  if (result.status === 'error') {
    return undefined
  }
  return result.data
}

export async function getBytes(oid: string): Promise<Blob> {
  const result = await axios(`/attachments/${oid}`, {
    method: 'get',
    responseType: 'blob'
  })
  return result.data as Blob
}

export async function getThumb(oid: string): Promise<Blob> {
  const result = await axios(`/attachments/${oid}/thumb`, {
    method: 'get',
    responseType: 'blob'
  })
  return result.data as Blob
}

export type AttachmentScope = 'mine' | 'all'

export async function list(
  scope: AttachmentScope = 'mine',
  filters: {
    purpose?: string
    status?: string
    page?: number
    size?: number
    entityType?: string
    entityOid?: string
  } = {}
) {
  const result = (
    await axios('/attachments', {
      params: {
        scope,
        purpose: filters.purpose,
        status: filters.status,
        page: filters.page,
        size: filters.size,
        entity_type: filters.entityType,
        entity_oid: filters.entityOid
      }
    })
  ).data as Response<AttachmentListItem[]> & {
    metadata: {
      size: number
    }
  }

  if (result.status === 'error') {
    throw new Error(result.message)
  }

  return {
    data: result.data,
    total: result.metadata.size
  }
}

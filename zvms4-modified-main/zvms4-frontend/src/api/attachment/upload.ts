import axios from '@/plugins/axios'
import type { AxiosProgressEvent } from 'axios'
import type {
  Response,
  AttachmentPurpose,
  AttachmentUploadResult
} from '@/../types'

export const VOLUNTEER_ORDER: AttachmentPurpose = 'volunteer_order'

export async function upload(
  files: File | FileList | File[],
  purpose: AttachmentPurpose = 'volunteer_order',
  entityType?: string,
  entityOid?: string,
  onUploadProgress?: (progressEvent: AxiosProgressEvent) => void
): Promise<AttachmentUploadResult[]> {
  const list = files instanceof File ? [files] : Array.from(files ?? [])
  if (list.length === 0) return []

  const form = new FormData()
  list.forEach((file) => form.append('file', file))
  form.append('purpose', purpose)
  if (entityType) form.append('entity_type', entityType)
  if (entityOid) form.append('entity_oid', entityOid)

  const result = (await axios('/attachments', {
    method: 'post',
    data: form,
    headers: {
      'Content-Type': undefined
    },
    onUploadProgress
  })).data as Response<AttachmentUploadResult | AttachmentUploadResult[]>

  if (result.status === 'error') {
    throw new Error(result.message)
  }

  const payload = result.data
  return Array.isArray(payload) ? payload : [payload]
}

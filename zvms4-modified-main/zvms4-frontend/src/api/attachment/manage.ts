import axios from '@/plugins/axios'
import type {
  Response,
  AttachmentMeta,
  SignedLink,
  OcrResult,
  VerifyPayload,
  BindPayload
} from '@/../types'

export async function signLink(oid: string): Promise<SignedLink> {
  const result = (await axios(`/attachments/${oid}/link`, {
    method: 'post'
  })).data as Response<SignedLink>
  if (result.status === 'error') {
    throw new Error(result.message)
  }
  return result.data
}

export async function startOcr(oid: string): Promise<OcrResult> {
  const result = (await axios(`/attachments/${oid}/ocr`, {
    method: 'post'
  })).data as Response<OcrResult>
  if (result.status === 'error') {
    throw new Error(result.message)
  }
  return result.data
}

export async function getOcr(oid: string): Promise<OcrResult | undefined> {
  const result = (await axios(`/attachments/${oid}/ocr`)).data as Response<OcrResult>
  if (result.status === 'error') {
    return undefined
  }
  return result.data
}

export async function verify(
  oid: string,
  payload: VerifyPayload
): Promise<AttachmentMeta> {
  const result = (await axios(`/attachments/${oid}/verify`, {
    method: 'post',
    data: payload
  })).data as Response<AttachmentMeta>
  if (result.status === 'error') {
    throw new Error(result.message)
  }
  return result.data
}

export async function bind(oid: string, payload: BindPayload): Promise<AttachmentMeta> {
  const result = (await axios(`/attachments/${oid}/bind`, {
    method: 'post',
    data: payload
  })).data as Response<AttachmentMeta>
  if (result.status === 'error') {
    throw new Error(result.message)
  }
  return result.data
}

export async function deleteOne(oid: string): Promise<void> {
  const result = (await axios(`/attachments/${oid}`, {
    method: 'delete'
  })).data as Response<unknown>
  if (result.status === 'error') {
    throw new Error(result.message)
  }
}

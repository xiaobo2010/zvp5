/**
 * 附件管理（admin）：列表、审计详情、裁决与删除。
 *
 * 与 read.ts 的「我的附件」分开：管理端固定 scope=all，后端会再判一次管理
 * 权限；列表只回元数据（不带 OCR 正文与事件流），审计详情才给 OCR 文本、
 * 逐行坐标与 FILE_EVENTS 时间线。
 *
 * 裁决与删除都是留痕动作，调用方拿到失败必须显式提示——这里统一把后端的
 * error 信封转成抛错，不静默吞掉。
 */
import axios from '@/plugins/axios'
import type {
  Response,
  AttachmentAuditDetail,
  AttachmentAuditListItem
} from '@/../types'

export interface AttachmentAuditQuery {
  /** 1 起算。 */
  page?: number
  /** 每页条数，默认 20，上限 100。 */
  size?: number
  /** 用途精确过滤（如 volunteer_order）。 */
  purpose?: string
  /** 状态精确过滤；不传时后端排除 deleted / rejected / quarantined。 */
  status?: string
  /** 类型精确过滤（original / thumb / derived）。 */
  kind?: string
  /** OCR 状态精确过滤（pending / processing / done / failed）。 */
  ocrStatus?: string
  /** 上传者 oid 精确过滤。 */
  ownerOid?: string
  /** 上传时间下界（ISO 串，含端点）。 */
  createdFrom?: string
  /** 上传时间上界（ISO 串，含端点）。 */
  createdTo?: string
}

export interface AttachmentAuditPage {
  data: AttachmentAuditListItem[]
  /** 总条数，来自响应体的 metadata.size。 */
  total: number
}

/** 裁决动作：通过 / 驳回。 */
export type AttachmentDecision = 'verified' | 'rejected'

export const ATTACHMENT_AUDIT_PERPAGE_DEFAULT = 20
export const ATTACHMENT_AUDIT_PERPAGE_MAX = 100

function clampPage(value?: number): number {
  const page = Number.isFinite(value) ? Math.trunc(value as number) : 1
  return Math.max(1, page)
}

function clampSize(value?: number): number {
  const size = Number.isFinite(value)
    ? Math.trunc(value as number)
    : ATTACHMENT_AUDIT_PERPAGE_DEFAULT
  return Math.min(Math.max(1, size), ATTACHMENT_AUDIT_PERPAGE_MAX)
}

/** 拉一页附件元数据（scope 固定 all，由后端再判管理权限）。 */
export async function listAuditAttachments(
  query: AttachmentAuditQuery = {}
): Promise<AttachmentAuditPage> {
  const params: Record<string, string | number> = {
    scope: 'all',
    page: clampPage(query.page),
    size: clampSize(query.size)
  }

  const purpose = query.purpose?.trim()
  if (purpose) {
    params.purpose = purpose
  }
  const status = query.status?.trim()
  if (status) {
    params.status = status
  }
  const kind = query.kind?.trim()
  if (kind) {
    params.kind = kind
  }
  const ocrStatus = query.ocrStatus?.trim()
  if (ocrStatus) {
    params.ocr_status = ocrStatus
  }
  const ownerOid = query.ownerOid?.trim()
  if (ownerOid) {
    params.owner_oid = ownerOid
  }
  const createdFrom = query.createdFrom?.trim()
  if (createdFrom) {
    params.created_from = createdFrom
  }
  const createdTo = query.createdTo?.trim()
  if (createdTo) {
    params.created_to = createdTo
  }

  const result = (
    await axios('/attachments', {
      method: 'get',
      params
    })
  ).data as Response<AttachmentAuditListItem[]> & {
    metadata?: { size?: number }
  }

  if (result.status === 'error') {
    throw new Error(result.message)
  }

  return {
    data: Array.isArray(result.data) ? result.data : [],
    total: result.metadata?.size ?? 0
  }
}

/** 读一条附件的审计详情（元数据 + 上传者 + OCR + 事件流）。 */
export async function getAttachmentAudit(oid: string): Promise<AttachmentAuditDetail> {
  const result = (
    await axios(`/attachments/${oid}/audit`, {
      method: 'get'
    })
  ).data as Response<AttachmentAuditDetail>

  if (result.status === 'error') {
    throw new Error(result.message)
  }
  return result.data
}

/** 裁决附件：通过（verified）或驳回（rejected），可附备注。
 *
 * ``verificationToken`` 是密码再认证签发的 5 分钟短令牌（``purpose=verify``），
 * 后端只认 body 里的 ``verification_token``；塞进 Authorization 会被当成登录态。
 */
export async function decideAttachment(
  oid: string,
  payload: { status: AttachmentDecision; note?: string },
  verificationToken?: string
): Promise<void> {
  const result = (
    await axios(`/attachments/${oid}/status`, {
      method: 'patch',
      data: {
        ...payload,
        ...(verificationToken ? { verification_token: verificationToken } : {})
      }
    })
  ).data as Response<unknown>

  if (result.status === 'error') {
    throw new Error(result.message)
  }
}

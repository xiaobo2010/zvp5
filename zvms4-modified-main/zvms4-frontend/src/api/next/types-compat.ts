/**
 * 新接口层用到的共享类型。
 *
 * 主分支已经把后端响应拆成更细的视图（附件审计、统一 Response 外壳等），
 * 但这些类型还没并进本仓库的 `types/`。为了不牵动既有页面，这里先给出
 * 最小可用的本地定义；等 `types/` 整体升级时再删掉本文件。
 */
export interface Response<T> {
  status: string
  code: number
  data: T
  message?: string
  detail?: string
}

export type AttachmentAuditStatus = 'pending' | 'approved' | 'rejected' | 'deleted' | 'quarantined'

export interface AttachmentAuditListItem {
  oid: string
  name: string
  purpose: string
  status: AttachmentAuditStatus
  kind: string
  size: number
  mime: string
  ocr_status: string
  uploader_oid: string
  uploader_name: string
  created_at: string
}

export interface AttachmentAuditEvent {
  at: string
  actor: string
  action: string
  detail?: string
}

export interface AttachmentAuditDetail extends AttachmentAuditListItem {
  ocr_text?: string | null
  ocr_lines?: { text: string; box?: number[] }[]
  events: AttachmentAuditEvent[]
  url?: string | null
  thumb_url?: string | null
}

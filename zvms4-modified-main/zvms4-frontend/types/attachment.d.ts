export type AttachmentPurpose = 'volunteer_order'

export type AttachmentStatus =
  | 'stored'
  | 'processing'
  | 'ready'
  | 'verified'
  | 'rejected'
  | 'quarantined'
  | 'deleted'

export type OcrStatus = 'pending' | 'processing' | 'done' | 'failed'

export interface AttachmentUploadResult {
  _id: string
  mime: string
  size: number
  width?: number
  height?: number
  ocr_status: OcrStatus
}

export interface AttachmentMeta extends AttachmentUploadResult {
  status?: AttachmentStatus
  purpose?: AttachmentPurpose | string
  entity_type?: string
  entity_oid?: string
  original_name?: string
  owner_oid?: string
  created_at?: string
  updated_at?: string
  deleted_at?: string
}

export interface AttachmentListItem extends AttachmentMeta {}

export interface SignedLink {
  url: string
  expires_at: string
}

export interface OcrLine {
  text: string
  confidence?: number | null
  /** 坐标随引擎而变，前端用 utils/ocrBox 归一后再画框。 */
  box?: unknown
}

export interface OcrResult {
  status: OcrStatus
  engine?: string
  engine_version?: string
  text?: string
  confidence?: number
  /** 逐行识别结果：恒存在（后端无行时给 []），坐标/置信度可能为空。 */
  lines?: OcrLine[]
  updated_at?: string
}

export interface VerifyPayload {
  decision: 'verified' | 'rejected'
  note?: string
}

export interface BindPayload {
  entity_type: string
  entity_oid: string
}

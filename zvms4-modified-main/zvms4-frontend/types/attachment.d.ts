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

/**
 * 附件管理（admin）列表项。
 *
 * 字段与 FILES 表的列一一对应：列表层只回元数据，不带 OCR 正文与事件流。
 * `size_bytes` 是后端的列名（与上传响应的 `size` 不是同一个字段，别混用）。
 */
export interface AttachmentAuditListItem {
  _id: string
  /** original / thumb / derived */
  kind?: string
  mime?: string
  size_bytes?: number
  width?: number | null
  height?: number | null
  purpose?: string
  original_name?: string
  entity_type?: string
  entity_oid?: string
  status?: AttachmentStatus
  ocr_status?: OcrStatus
  created_at?: string
  owner_oid?: string
  has_ocr?: boolean
  deleted_at?: string
}

/** FILE_EVENTS 一行：附件的审计时间线（append-only）。 */
export interface AttachmentAuditEvent {
  _id?: string
  action?: string
  from_status?: string
  to_status?: string
  actor_oid?: string
  detail?: unknown
  created_at?: string
}

/** 上传者：后端给嵌套形态（`_id` / `student_id` / `name`）与扁平字段两套。 */
export interface AttachmentAuditOwner {
  _id?: string
  student_id?: string
  name?: string
}

/**
 * 审计详情：元数据 + 上传者 + OCR 文本 + 逐行坐标 + 事件流。
 * 上传者的学号/姓名字段后端可能给 `owner_name` / `owner_id` 之一，
 * 这里都声明成可选，页面按优先级兜底读。
 */
export interface AttachmentAuditDetail extends AttachmentAuditListItem {
  owner?: AttachmentAuditOwner | null
  owner_name?: string
  owner_id?: string
  owner_xuehai_id?: string
  ocr_text?: string
  ocr_engine?: string
  ocr_confidence?: number
  lines?: OcrLine[]
  events?: AttachmentAuditEvent[]
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

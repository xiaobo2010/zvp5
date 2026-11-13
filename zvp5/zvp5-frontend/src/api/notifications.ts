import type { Response } from '@/../types'
import axios from '@/plugins/axios.ts'

/**
 * 站内通知接口（契约见 backend `docs/NOTIFICATIONS_API.md`）。
 *
 * 服务端负责通知内容、版本号与「已阅」记录（跟着账号走，换设备登录同样生效）；
 * 「不再显示」仍是设备级偏好，只存在于客户端本地。
 */
export interface NotificationRecord {
  id: string
  title: string
  content: string
  /** 单条通知的修订号，每次编辑/停用/恢复都会 +1。 */
  version: number
  /** 当前内容的哈希标识，由服务端计算；老响应可能缺省。 */
  content_hash?: string
  /** 发布人用户 oid（创建时写入，之后不可变）。 */
  author_id: string
  /** 发布当时的显示名（之后不可变）。 */
  author_name: string
  created_at: string | null
  updated_at: string | null
  active: boolean
}


export interface NotificationListData {
  /** 全局通知版本，仅用于和本地缓存比较，不能代替单条 read 判定。 */
  version: number
  notifications: NotificationRecord[]
}

export interface NotificationMutationData {
  notification: NotificationRecord
  /** 写入后的全局版本。 */
  version: number
}

/** 当前登录用户的已读映射：通知 oid -> 读到的单条版本号。 */
export interface NotificationReadMap {
  read: Record<string, number>
}

/** 标记已读的回执：服务端记下的单条版本号。 */
export interface NotificationReadReceipt {
  oid: string
  /** 服务端认定的该通知当前版本。 */
  version: number
}

export interface CreateNotificationInput {
  title: string
  content: string
}

export interface UpdateNotificationInput {
  title?: string
  content?: string
  active?: boolean
}

function unwrap<T>(payload: Response<T>): T {
  if (payload.status !== 'ok') {
    throw payload
  }
  return payload.data
}

/** 用户端：当前有效通知 + 全局版本。任何已登录用户可读。 */
export async function listActive(signal?: AbortSignal): Promise<NotificationListData> {
  const response = await axios({
    method: 'get',
    url: '/v1/notifications',
    signal
  })
  return unwrap(response.data as Response<NotificationListData>)
}

/**
 * 用户端：当前账号的已读映射。
 *
 * 已读状态存在服务端，跟着账号走：换设备登录同样生效。
 * 返回的 read[oid] 是「读到的单条版本号」；read[oid] >= 通知当前版本 即为已读，
 * 通知被编辑（version + 1）后会重新变回未读。
 */
export async function listRead(signal?: AbortSignal): Promise<Record<string, number>> {
  const response = await axios({
    method: 'get',
    url: '/v1/notifications/read',
    signal
  })
  const data = unwrap(response.data as Response<NotificationReadMap>)
  return data?.read ?? {}
}

/**
 * 用户端：标记某条通知已读（幂等 upsert）。
 *
 * 任何已登录用户可调用；通知不存在时服务端返回 404。
 * 回执里的 version 是服务端认定的该通知当前版本，前端据此对齐本地已读映射。
 */
export async function markRead(oid: string): Promise<NotificationReadReceipt> {
  const response = await axios({
    method: 'post',
    url: `/v1/notifications/${oid}/read`
  })
  return unwrap(response.data as Response<NotificationReadReceipt>)
}

/** 管理端：包含停用项的通知列表。仅严格 admin 可用。 */
export async function listAll(): Promise<NotificationListData> {
  const response = await axios({
    method: 'get',
    url: '/v1/notifications/admin'
  })
  return unwrap(response.data as Response<NotificationListData>)
}

/** 创建即为发送；发布人由服务端从当前登录用户解析。 */
export async function createNotification(
  input: CreateNotificationInput,
  verificationToken?: string
): Promise<NotificationMutationData> {
  const response = await axios({
    method: 'post',
    url: '/v1/notifications',
    data: { ...input, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
  return unwrap(response.data as Response<NotificationMutationData>)
}

/** 部分更新标题/正文/启停状态。 */
export async function updateNotification(
  id: string,
  input: UpdateNotificationInput,
  verificationToken?: string
): Promise<NotificationMutationData> {
  const response = await axios({
    method: 'put',
    url: `/v1/notifications/${id}`,
    data: { ...input, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
  return unwrap(response.data as Response<NotificationMutationData>)
}

/** 硬删除。管理界面通常优先停用，以保留通知记录。 */
export async function deleteNotification(id: string, verificationToken?: string): Promise<void> {
  await axios({
    method: 'delete',
    url: `/v1/notifications/${id}`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
}

export type NotificationVersionOp = 'create' | 'update' | 'delete' | 'rollback'

/** 历史版本快照，按 version 倒序（契约见后端 system_notifications 路由）。 */
export interface NotificationVersionItem {
  version: number
  content_hash: string
  title: string
  content: string
  active: boolean
  op: NotificationVersionOp
  /** 回滚操作指向的源版本号，其余 op 恒为 null。 */
  source_version: number | null
  author_id: string | null
  author_name: string
  created_at: string
}

export interface NotificationVersionsData {
  versions: NotificationVersionItem[]
  /** 当前生效版本号。 */
  version: number
}

export interface NotificationRollbackData {
  notification: NotificationRecord
  version: number
  content_hash: string
}

/** HTTP 非 2xx 时 axios 拦截器 reject 的是 AxiosError，用 HTTP 状态区分场景。 */
export function httpStatusOf(error: unknown): number | null {
  if (error && typeof error === 'object' && 'isAxiosError' in error) {
    const status = (error as { response?: { status?: number } }).response?.status
    return typeof status === 'number' ? status : null
  }
  return null
}

/** 管理端：读取单条通知的全部版本快照。仅严格 admin 可用。 */
export async function listVersions(id: string): Promise<NotificationVersionsData> {
  const response = await axios({
    method: 'get',
    url: `/v1/notifications/${id}/versions`
  })
  return unwrap(response.data as Response<NotificationVersionsData>)
}

/** 回滚到指定版本：产生一个新版本，不改写历史。 */
export async function rollbackNotification(
  id: string,
  version: number,
  verificationToken?: string
): Promise<NotificationRollbackData> {
  const response = await axios({
    method: 'post',
    url: `/v1/notifications/${id}/rollback`,
    data: { version, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
  return unwrap(response.data as Response<NotificationRollbackData>)
}

import type { Response } from '@/../types'
import axios from '@/plugins/axios.ts'

/**
 * 站内通知接口（契约见 backend `docs/NOTIFICATIONS_API.md`）。
 *
 * 服务端只负责通知内容和版本号；「已阅 / 不再显示」是客户端本地状态，
 * 这里没有任何标记阅读的请求。
 */
export interface NotificationRecord {
  id: string
  title: string
  content: string
  /** 单条通知的修订号，每次编辑/停用/恢复都会 +1。 */
  version: number
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
    url: '/v3/notifications',
    signal
  })
  return unwrap(response.data as Response<NotificationListData>)
}

/** 管理端：包含停用项的通知列表。仅严格 admin 可用。 */
export async function listAll(): Promise<NotificationListData> {
  const response = await axios({
    method: 'get',
    url: '/v3/notifications/admin'
  })
  return unwrap(response.data as Response<NotificationListData>)
}

/** 创建即为发送；发布人由服务端从当前登录用户解析。 */
export async function createNotification(
  input: CreateNotificationInput
): Promise<NotificationMutationData> {
  const response = await axios({
    method: 'post',
    url: '/v3/notifications',
    data: input
  })
  return unwrap(response.data as Response<NotificationMutationData>)
}

/** 部分更新标题/正文/启停状态。 */
export async function updateNotification(
  id: string,
  input: UpdateNotificationInput
): Promise<NotificationMutationData> {
  const response = await axios({
    method: 'put',
    url: `/v3/notifications/${id}`,
    data: input
  })
  return unwrap(response.data as Response<NotificationMutationData>)
}

/** 硬删除。管理界面通常优先停用，以保留通知记录。 */
export async function deleteNotification(id: string): Promise<void> {
  await axios({
    method: 'delete',
    url: `/v3/notifications/${id}`
  })
}

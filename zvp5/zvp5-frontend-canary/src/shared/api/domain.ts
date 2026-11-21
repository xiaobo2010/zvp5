import { request, unwrap } from '@/shared/http/client'
import type {
  Activity,
  ActivityPage,
  MemberPage,
  NotificationItem,
  TimePercentile,
  UserActivityPage,
  UserTime,
  UserTimeStatistics
} from '@/types/domain'

export async function fetchUserTime(userOid: string): Promise<UserTime> {
  return request<UserTime>({ url: `/v1/users/${userOid}/time`, method: 'GET' })
}

export async function fetchUserTimeStatistics(userOid: string): Promise<UserTimeStatistics> {
  return request<UserTimeStatistics>({
    url: `/v1/users/${userOid}/time_statistics`,
    method: 'GET'
  })
}

export interface UserActivityQuery {
  page?: number
  perpage?: number
  search?: string
  sort?: string
  asc?: boolean
}

export async function fetchUserActivities(
  userOid: string,
  params: UserActivityQuery = {}
): Promise<UserActivityPage> {
  return request<UserActivityPage>({
    url: `/v1/users/${userOid}/activities`,
    method: 'GET',
    params: {
      page: params.page ?? 1,
      perpage: params.perpage ?? 10,
      search: params.search ?? '',
      sort: params.sort ?? '_id',
      asc: params.asc ?? false
    }
  })
}

export interface ActivityQuery {
  page?: number
  size?: number
  query?: string
  type?: string
  status?: string
  sort?: string
  asc?: boolean
}

export async function fetchActivities(params: ActivityQuery = {}): Promise<ActivityPage> {
  return request<ActivityPage>({
    url: '/v1/activities',
    method: 'GET',
    params: {
      page: params.page ?? 1,
      size: params.size ?? 12,
      query: params.query ?? '',
      type: params.type ?? '',
      status: params.status ?? '',
      ...(params.sort ? { sort: params.sort, asc: params.asc ?? false } : {})
    }
  })
}

export async function fetchActivity(id: string): Promise<{
  activity: Activity
  members_count: number
}> {
  return request({ url: `/v1/activities/${id}`, method: 'GET' })
}

/** 按 OID 取用户摘要；活动里的 appointee / approver 只有 OID，需要一个名字。 */
export async function fetchUserBrief(oid: string): Promise<{ _id: string; id: string; name: string }> {
  const payload = await request<unknown>({ url: `/v1/users/${oid}`, method: 'GET' })
  return unwrap<{ _id: string; id: string; name: string }>(payload)
}

export async function fetchActivityMembers(
  id: string,
  page = 1,
  size = 20,
  query = ''
): Promise<MemberPage> {
  return request<MemberPage>({
    url: `/v1/activities/${id}/members`,
    method: 'GET',
    params: { page, size, query }
  })
}

export async function fetchNotifications(): Promise<{
  version: number
  notifications: NotificationItem[]
}> {
  const payload = await request<unknown>({ url: '/v1/notifications', method: 'GET' })
  const data = unwrap<{ version: number; notifications: NotificationItem[] }>(payload)
  return { version: data.version ?? 0, notifications: data.notifications ?? [] }
}

export async function fetchGroup(
  groupOid: string
): Promise<{ _id: string; name: string; type?: string }> {
  const payload = await request<unknown>({ url: `/v1/groups/${groupOid}`, method: 'GET' })
  return unwrap<{ _id: string; name: string; type?: string }>(payload)
}

export async function fetchNotificationReads(): Promise<Record<string, number>> {
  const payload = await request<unknown>({ url: '/v1/notifications/read', method: 'GET' })
  const data = unwrap<{ read: Record<string, number> }>(payload)
  return data.read ?? {}
}

export async function markNotificationRead(oid: string): Promise<void> {
  await request({ url: `/v1/notifications/${oid}/read`, method: 'POST' })
}

export type { TimePercentile }

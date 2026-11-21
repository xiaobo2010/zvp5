import { request } from '@/shared/http/client'
import type {
  ActivityStatus,
  AdminUserRow,
  GroupMemberRow,
  GroupRow,
  TimeMode,
  UserRole
} from '@/types/domain'

/** 活动审核：待审核 → 生效 / 驳回。后端用 CAS 判并发，冲突返回 409。 */
export async function updateActivityStatus(
  activityId: string,
  status: Exclude<ActivityStatus, 'draft'>
): Promise<void> {
  await request({
    url: `/v1/activities/${activityId}/status`,
    method: 'PUT',
    data: { status }
  })
}

/** 单个义工的审核状态（成员记录 id，不是用户 id）。 */
export async function updateMemberStatus(
  activityId: string,
  memberId: string,
  status: Exclude<ActivityStatus, 'draft'>
): Promise<{ id: string; status: string }> {
  return request({
    url: `/v1/activities/${activityId}/members/${memberId}/status`,
    method: 'PUT',
    data: { status }
  })
}

/** 修改义工时长与时长类型。后端要求 duration > 0。 */
export async function updateMemberRecord(
  activityId: string,
  memberId: string,
  payload: { duration: number; mode: TimeMode }
): Promise<void> {
  await request({
    url: `/v1/activities/${activityId}/members/${memberId}/record`,
    method: 'PUT',
    data: payload
  })
}

/** 批量登记义工：显式传成员用户 OID 列表，不做「学生自主报名」。 */
export async function addActivityMembers(
  activityId: string,
  members: Array<{ member: string; duration: number; mode: TimeMode }>
): Promise<void> {
  await request({
    url: `/v1/activities/${activityId}/members`,
    method: 'POST',
    data: members
  })
}

export async function removeActivityMember(activityId: string, memberId: string): Promise<void> {
  await request({
    url: `/v1/activities/${activityId}/members/${memberId}`,
    method: 'DELETE'
  })
}

/** 用户列表：`role` 非空时后端只允许 admin 使用。 */
export async function fetchUsers(params: {
  page: number
  size: number
  query: string
  role: string
}): Promise<{ data: AdminUserRow[]; total: number }> {
  return request({
    url: '/v1/users',
    method: 'GET',
    params: { page: params.page, size: params.size, query: params.query, role: params.role }
  })
}

/** 修改身份。后端禁止操作 admin 级账号（403），角色白名单由后端校验。 */
export async function updateUserRoles(userId: string, roles: UserRole[]): Promise<void> {
  await request({
    url: `/v1/users/${userId}/roles`,
    method: 'PUT',
    data: { roles }
  })
}

/** 封禁 / 解封。`duration_days` 不传＝永久；解封方向不能带时长。 */
export async function setUserBanned(
  userId: string,
  payload: { banned: boolean; reason?: string; duration_days?: number }
): Promise<{ banned: boolean; ban_until: string | null; permanent: boolean }> {
  const body = await request<unknown>({
    url: `/v1/users/${userId}/banned`,
    method: 'PUT',
    data: payload
  })
  const envelope = body as { data?: { banned: boolean; ban_until: string | null; permanent: boolean } }
  return envelope.data ?? { banned: payload.banned, ban_until: null, permanent: false }
}

export async function fetchGroups(params: {
  page: number
  size: number
  query: string
}): Promise<{ data: GroupRow[]; total: number }> {
  return request({
    url: '/v1/groups',
    method: 'GET',
    params: { page: params.page, size: params.size, query: params.query }
  })
}

export async function createGroup(name: string): Promise<{ id: string }> {
  return request({ url: '/v1/groups', method: 'POST', data: { name } })
}

export async function renameGroup(groupId: string, name: string): Promise<void> {
  await request({ url: `/v1/groups/${groupId}/name`, method: 'PUT', data: { name } })
}

export async function fetchGroupMembers(
  groupId: string,
  page: number,
  size: number,
  query = ''
): Promise<{ data: GroupMemberRow[]; total: number }> {
  return request({
    url: `/v1/groups/${groupId}/users`,
    method: 'GET',
    params: { page, size, query }
  })
}

export async function addGroupMember(groupId: string, userId: string): Promise<void> {
  await request({ url: `/v1/groups/${groupId}/users/${userId}`, method: 'POST' })
}

export async function removeGroupMember(groupId: string, userId: string): Promise<void> {
  await request({ url: `/v1/groups/${groupId}/users/${userId}`, method: 'DELETE' })
}

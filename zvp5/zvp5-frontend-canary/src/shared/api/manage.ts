import { request } from '@/shared/http/client'
import type { ActivityStatus, TimeMode } from '@/types/domain'

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

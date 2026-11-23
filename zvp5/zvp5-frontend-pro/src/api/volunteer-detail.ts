import { ApiError, apiRequest, apiVoid, buildQuery } from '@/api/http'

export type VolunteerActivityStatus = 'draft' | 'pending' | 'refused' | 'effective'
export type VolunteerMemberStatus = 'pending' | 'effective' | 'refused'
export type VolunteerRegistrationMode =
  | 'on-campus'
  | 'off-campus'
  | 'social-practice'
export type VolunteerActivityType =
  | 'on-campus'
  | 'off-campus'
  | 'social-practice'
  | 'hybrid'

/** One activity row as returned by `GET /api/v1/activities/{id}`. */
export interface VolunteerActivity {
  _id: string
  name: string
  description?: string
  type: VolunteerActivityType
  status: VolunteerActivityStatus | string
  date: string
  place?: string
  creator?: string
  creator_name?: string
  origin?: string
}

export interface VolunteerActivityDetail {
  activity: VolunteerActivity
  members_count: number
}

/** One roster row from the v3 member list (already joined with the user). */
export interface VolunteerMember {
  _id: string
  activity: string
  member: string
  member_name?: string
  mode: VolunteerRegistrationMode
  status: VolunteerMemberStatus
  duration: number
  user?: {
    _id?: string
    id?: string | number
    name?: string
    class_id?: string
  } | null
}

export interface VolunteerMemberPage {
  members: VolunteerMember[]
  total: number
}

export interface VolunteerUserSummary {
  _id: string
  id: string | number
  name: string
  class_id?: string
}

/**
 * The v3 roster endpoint rejects `size > 100` with a 422, so the UI clamps the
 * page size before sending instead of relying on the server to trim it.
 */
const MEMBER_PAGE_MAX = 100

function normalizePage(value: number): number {
  const page = Math.trunc(Number(value))
  return Number.isFinite(page) && page > 0 ? page : 1
}

function normalizeSize(value: number): number {
  const size = Math.trunc(Number(value))
  if (!Number.isFinite(size) || size < 1) return 10
  return Math.min(size, MEMBER_PAGE_MAX)
}

export async function fetchVolunteerDetail(
  activityId: string
): Promise<VolunteerActivityDetail> {
  const result = await apiRequest<Partial<VolunteerActivityDetail>>(
    `/v1/activities/${encodeURIComponent(activityId)}`
  )
  if (!result?.activity) {
    // The endpoint always returns `{activity, members_count}`; a missing
    // activity means a broken/legacy payload, not a valid empty detail.
    throw new ApiError('活动资料不完整，请稍后重试。', 502)
  }
  return {
    activity: result.activity,
    members_count: Number(result.members_count) || 0
  }
}

export async function fetchVolunteerMembers(
  activityId: string,
  page = 1,
  size = 10,
  query = ''
): Promise<VolunteerMemberPage> {
  const result = await apiRequest<VolunteerMemberPage>(
    `/v1/activities/${encodeURIComponent(activityId)}/members${buildQuery({
      page: normalizePage(page),
      size: normalizeSize(size),
      query
    })}`
  )
  if (!Array.isArray(result?.members)) {
    throw new ApiError('参与名单数据不完整，请稍后重试。', 502)
  }
  return {
    members: result.members,
    total: Number(result?.total) || 0
  }
}

/**
 * Change an activity's review status.
 *
 * Backend rules:
 * - only `admin` / `department`;
 * - `department` may only move a *pending* activity (to effective/refused);
 * - `admin` may change a non-pending status too;
 * - same status -> 400, concurrent change -> 409.
 */
export async function updateVolunteerActivityStatus(
  activityId: string,
  status: Exclude<VolunteerActivityStatus, 'draft'>
): Promise<void> {
  await apiVoid(`/v1/activities/${encodeURIComponent(activityId)}/status`, {
    method: 'PUT',
    body: JSON.stringify({ status })
  })
}

/**
 * Read a user summary for the creator label. `/api/v1/users/{oid}` is readable
 * by every authenticated account (`get_user_v3` has no extra gate), but the
 * caller treats a failure as "name unavailable" rather than fatal.
 */
export async function fetchVolunteerUser(
  oid: string
): Promise<VolunteerUserSummary | null> {
  if (!oid) return null
  const result = await apiRequest<VolunteerUserSummary | null>(
    `/v1/users/${encodeURIComponent(oid)}`
  )
  return result ?? null
}

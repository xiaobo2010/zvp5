// Read-only user detail for the Pro platform console.
//
// These backend routes already exist on the main API and only read data:
//   GET /v1/users/{oid}            (v3) profile for any authenticated account
//   GET /v1/users/{oid}/time       (v2) raw hours for that user
//   GET /v1/users/{oid}/activities (v2) member records, paginated by `perpage`
//   GET /v1/groups/{oid}           (v2) class name, admin/department or own class
//
// `users_v2_router` is registered before `users_v3_router` on the same prefix,
// so `/time` and `/activities` resolve to the v2 shapes typed below, while the
// bare profile path only exists on v3. Nothing here writes: the platform console
// keeps its read-only contract and the server still owns every permission gate.

import { apiRequest, buildQuery } from './http'

/** Mirrors the backend's `perpage <= 100` cap on the v2 member list. */
export const MANAGE_USER_ACTIVITY_PAGE_MAX = 100

export interface AdminUserProfile {
  _id: string
  id: string | number
  name: string
  roles?: string[]
  class_id?: string
  banned?: boolean
  ban_reason?: string | null
  ban_until?: string | null
  ban_permanent?: boolean
}

export interface AdminUserTime {
  'on-campus': number
  'off-campus': number
  'social-practice': number
}

export interface AdminUserActivityMember {
  _id?: string
  status?: string
  mode?: string
  duration?: number
}

export interface AdminUserActivity {
  _id: string
  name?: string
  date?: string
  status?: string
  type?: string
  place?: string
  mine?: AdminUserActivityMember | null
}

export interface AdminUserActivityPage {
  page: number
  perpage: number
  total: number
  activities: AdminUserActivity[]
}

export function fetchAdminUserProfile(oid: string): Promise<AdminUserProfile> {
  return apiRequest<AdminUserProfile>(`/v1/users/${encodeURIComponent(oid)}`)
}

export function fetchAdminUserTime(oid: string): Promise<AdminUserTime> {
  return apiRequest<AdminUserTime>(`/v1/users/${encodeURIComponent(oid)}/time`)
}

/**
 * Paginate one user's activity records.
 *
 * `perpage` (not `size`) is the v2 parameter name; values above 100 are clamped
 * here so a UI mistake cannot turn into a 422 instead of a page.
 */
export function fetchAdminUserActivities(
  oid: string,
  page = 1,
  perpage = 10,
  search = ''
): Promise<AdminUserActivityPage> {
  const safePage = Number.isFinite(page) && page > 0 ? Math.trunc(page) : 1
  const safeSize = Math.min(
    MANAGE_USER_ACTIVITY_PAGE_MAX,
    Math.max(1, Number.isFinite(perpage) ? Math.trunc(perpage) : 10)
  )
  return apiRequest<AdminUserActivityPage>(
    `/v1/users/${encodeURIComponent(oid)}/activities${buildQuery({
      page: safePage,
      perpage: safeSize,
      search: search.trim(),
      sort: '_id',
      asc: false
    })}`
  )
}

/**
 * Resolve a class name for display. A failed lookup is cosmetic only, so the
 * caller keeps the raw class id (or its list-row fallback) instead of an error.
 */
export async function fetchAdminGroupName(groupId: string): Promise<string> {
  if (!groupId) return ''
  try {
    const group = await apiRequest<{ name?: string } | null>(
      `/v1/groups/${encodeURIComponent(groupId)}`
    )
    return typeof group?.name === 'string' ? group.name.trim() : ''
  } catch {
    return ''
  }
}

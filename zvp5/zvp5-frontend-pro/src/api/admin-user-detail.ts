// User detail API client for the Pro platform console.
//
// These backend routes already exist on the main API and only read data:
//   GET /v1/users/{oid}            (v3) profile for any authenticated account
//   GET /v1/users/{oid}/time       (v2) raw hours for that user
//   GET /v1/users/{oid}/activities (v2) member records, paginated by `perpage`
//   GET /v1/groups/{oid}           (v2) class name, admin/department or own class
//
// `users_v2_router` is registered before `users_v3_router` on the same prefix,
// so `/time` and `/activities` resolve to the v2 shapes typed below, while the
// bare profile path only exists on v3. The profile reads below are side-effect
// free; the admin edit helpers at the bottom call the existing v3 write routes
// and rely on the server for every permission gate.

import { apiRequest, apiVoid, buildQuery } from './http'

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

/* -------------------------------------------------------------------------- */
/* Admin edits                                                                 */
/* -------------------------------------------------------------------------- */

/** Roles accepted by `PUT /v1/users/{id}/roles` (backend Literal allowlist). */
export const ADMIN_EDITABLE_ROLES = ['student', 'secretary', 'department', 'admin'] as const
export type AdminEditableRole = (typeof ADMIN_EDITABLE_ROLES)[number]

/**
 * `PUT /v1/users/{oid}/roles` (admin only). The backend rejects changing
 * another admin or the operator's own roles with 403.
 */
export function updateUserRoles(oid: string, roles: string[]): Promise<void> {
  return apiVoid(`/v1/users/${encodeURIComponent(oid)}/roles`, {
    method: 'PUT',
    body: JSON.stringify({ roles })
  })
}

/**
 * `PUT /v1/users/{oid}/student-id` (admin only). The student id is the login
 * name; a successful write rotates the target's session id.
 */
export function updateUserStudentId(oid: string, id: string): Promise<void> {
  return apiVoid(`/v1/users/${encodeURIComponent(oid)}/student-id`, {
    method: 'PUT',
    body: JSON.stringify({ id })
  })
}

/** Set a user's class: `POST /v1/groups/{classId}/users/{userId}`. */
export function setUserClass(classId: string, userId: string): Promise<void> {
  return apiVoid(
    `/v1/groups/${encodeURIComponent(classId)}/users/${encodeURIComponent(userId)}`,
    { method: 'POST' }
  )
}

/** Clear a user's class: `DELETE /v1/groups/{oldClassId}/users/{userId}`. */
export function clearUserClass(classId: string, userId: string): Promise<void> {
  return apiVoid(
    `/v1/groups/${encodeURIComponent(classId)}/users/${encodeURIComponent(userId)}`,
    { method: 'DELETE' }
  )
}

export interface TempPasswordResult {
  /** Plaintext 8-digit code. Only returned once; the server stores a hash. */
  code: string
  expires_at: string | null
  target?: {
    _id?: string
    id?: string | number
    name?: string
  }
}

export interface AdminUserBanResult {
  banned: boolean
  /** ISO timestamp, or null for a permanent ban / after an unban. */
  ban_until: string | null
  permanent: boolean
}

export interface AdminUserBanInput {
  banned: boolean
  /** Required by the backend for a ban, 1–200 characters after trimming. */
  reason?: string
  /** Omitted = permanent; 1–36500 days when present. */
  duration_days?: number
}

/**
 * `PUT /v1/users/{oid}/banned` (admin only).
 *
 * A ban rotates the target's `session_id`, so the account is logged out; an
 * unban carries no reason or duration. The backend rejects banning yourself
 * and banning another admin, and 422s a duration on the unban direction.
 */
export function setUserBanned(
  oid: string,
  input: AdminUserBanInput
): Promise<AdminUserBanResult> {
  return apiRequest<AdminUserBanResult>(`/v1/users/${encodeURIComponent(oid)}/banned`, {
    method: 'PUT',
    body: JSON.stringify(input)
  })
}

/**
 * `DELETE /v1/users/{oid}` (admin only).
 *
 * This is a hard delete: the backend archives the user's rows into `user_trash`
 * (30-day retention) and removes them in one transaction. It requires the
 * password-verification token minted by `verifyAdminPassword` (403 without it)
 * and rejects deleting the operator's own account (403).
 */
export function deleteAdminUser(oid: string, verificationToken: string): Promise<void> {
  return apiVoid(`/v1/users/${encodeURIComponent(oid)}`, {
    method: 'DELETE',
    body: JSON.stringify({ verification_token: verificationToken })
  })
}

/**
 * `POST /v1/users/{oid}/temp-password` with a password-verification token.
 *
 * The token must be minted by the operator (`verifyPassword(own id, password)`);
 * a password is never sent to this endpoint.
 */
export function createUserTempPassword(
  oid: string,
  verificationToken: string
): Promise<TempPasswordResult> {
  return apiRequest<TempPasswordResult>(
    `/v1/users/${encodeURIComponent(oid)}/temp-password`,
    {
      method: 'POST',
      body: JSON.stringify({ verification_token: verificationToken })
    }
  )
}

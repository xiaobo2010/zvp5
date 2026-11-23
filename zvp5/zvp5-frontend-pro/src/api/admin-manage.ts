// Platform-console API client for the standalone Pro app.
//
// Every route below already exists on the main backend under `/api`; the
// helpers in `http.ts` attach the Bearer token and unwrap the legacy
// `{ status, data }` envelope. The Pro client stays dependency-free (fetch).

import {
  apiList,
  apiRequest,
  buildQuery,
  jsonBody,
  type ListResult
} from './http'

/* -------------------------------------------------------------------------- */
/* Shared row shapes                                                           */
/* -------------------------------------------------------------------------- */

export interface AdminUser {
  _id: string
  id: string | number
  name: string
  roles?: string[]
  class_id?: string
  class_name?: string
  banned?: boolean
  group?: string[]
}

/** `/v1/users` and `/v1/groups` paginate with a flat `{ data, total }` body. */
export interface AdminPage<T> {
  data: T[]
  total: number
}

export interface AdminGroup {
  _id: string
  name: string
  type?: string
  description?: string
  permissions?: string[]
}

export interface AdminNotification {
  id: string
  title: string
  content: string
  version?: number
  content_hash?: string
  author_id?: string
  author_name?: string
  created_at?: string | null
  updated_at?: string | null
  active: boolean
}

export interface AdminNotificationList {
  version: number
  notifications: AdminNotification[]
}

export interface AdminNotificationMutation {
  notification: AdminNotification
  version: number
}

export interface AdminRolePolicy {
  role: string
  features: Record<string, boolean>
  quotas: Record<string, number | null>
  updated_at?: string | null
  updated_by?: string | null
}

export interface AdminRolePolicies {
  roles: AdminRolePolicy[]
  editable_roles?: string[]
  feature_keys?: string[]
}

export type AdminSettingValue = unknown

export interface AdminSettingItem {
  key: string
  value: AdminSettingValue
  type: 'int' | 'bool' | 'str' | 'list' | 'dict' | string
  group: string
  label: string
  description?: string | null
  min?: number | null
  max?: number | null
  options?: string[] | null
  updated_at?: string | null
  updated_by?: string | null
}

export interface AdminSettings {
  items: AdminSettingItem[]
  defaults?: Record<string, AdminSettingValue>
}

export interface AdminLog {
  _id: string
  url?: string
  user?: string
  user_name?: string
  user_id?: string
  type?: string
  level?: string
  data?: string
  timestamp?: number
}

export interface AdminBanRule {
  code: string
  name: string
  trigger?: string
  trigger_name?: string
  enabled: boolean
  builtin?: boolean
  config?: Record<string, unknown>
  penalties?: (number | null)[]
  updated_at?: string
}

export interface AdminBanRecord {
  _id: string
  user_oid?: string
  user_name?: string
  user_id?: string
  source?: 'admin' | 'rule'
  rule_code?: string
  reason?: string
  permanent?: boolean
  started_at?: string
  expires_at?: string | null
  operator_name?: string
}

export interface AdminAttachment {
  _id: string
  kind?: string
  mime?: string
  size_bytes?: number
  original_name?: string
  purpose?: string
  status?: string
  ocr_status?: string
  owner_oid?: string
  created_at?: string | null
  entity_type?: string
  entity_oid?: string
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

interface ActivityCountPage {
  activities?: unknown[]
  total?: number
}

export async function fetchUserCount(): Promise<number> {
  const data = await apiRequest<AdminPage<AdminUser>>(
    `/v1/users${buildQuery({ page: 1, size: 1 })}`
  )
  const total = Number(data.total)
  return Number.isFinite(total) ? total : 0
}

export async function fetchGroupCount(): Promise<number> {
  const data = await apiRequest<AdminPage<AdminGroup>>(
    `/v1/groups${buildQuery({ page: 1, size: 1 })}`
  )
  const total = Number(data.total)
  return Number.isFinite(total) ? total : 0
}

// The v3 GET route returns `{ activities, total }` and uses `size`.
export async function fetchActivityCount(): Promise<number> {
  const data = await apiRequest<ActivityCountPage>(
    `/v1/activities${buildQuery({ page: 1, size: 1 })}`
  )
  const total = Number(data.total)
  return Number.isFinite(total) ? total : 0
}

/* -------------------------------------------------------------------------- */
/* Users                                                                       */
/* -------------------------------------------------------------------------- */

export function fetchUsers(
  page = 1,
  size = 10,
  query = '',
  role = ''
): Promise<AdminPage<AdminUser>> {
  return apiRequest<AdminPage<AdminUser>>(
    `/v1/users${buildQuery({ page, size, query, role })}`
  )
}

/**
 * Class roster for secretaries (and any same-class reader). The backend v3
 * route lists live members by `class_id`; it is the only user-list call a
 * secretary may make because `/v1/users` is admin/department only.
 */
export function fetchGroupUsers(
  groupId: string,
  page = 1,
  size = 10,
  query = ''
): Promise<AdminPage<AdminUser>> {
  return apiRequest<AdminPage<AdminUser>>(
    `/v1/groups/${encodeURIComponent(groupId)}/users${buildQuery({ page, size, query })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Theme usage statistics (admin only)                                         */
/* -------------------------------------------------------------------------- */

export interface AdminThemeStatItem {
  id: string
  label: string
  count: number
  percent: number
}

export interface AdminThemeStats {
  total: number
  items: AdminThemeStatItem[]
  /** Accounts whose stored theme id is no longer in the registry. */
  other: number
}

export function fetchThemeStats(): Promise<AdminThemeStats> {
  return apiRequest<AdminThemeStats>('/v1/themes/stats')
}

/* -------------------------------------------------------------------------- */
/* Groups                                                                      */
/* -------------------------------------------------------------------------- */

export function fetchGroups(
  page = 1,
  size = 10,
  query = ''
): Promise<AdminPage<AdminGroup>> {
  return apiRequest<AdminPage<AdminGroup>>(
    `/v1/groups${buildQuery({ page, size, query })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Notifications                                                               */
/* -------------------------------------------------------------------------- */

export function fetchAdminNotifications(): Promise<AdminNotificationList> {
  return apiRequest<AdminNotificationList>('/v1/notifications/admin')
}

export function createNotification(
  title: string,
  content: string,
  verificationToken: string
): Promise<AdminNotificationMutation> {
  return apiRequest<AdminNotificationMutation>(
    '/v1/notifications',
    jsonBody({ title, content, verification_token: verificationToken })
  )
}

export function updateNotificationActive(
  id: string,
  active: boolean,
  verificationToken: string
): Promise<AdminNotificationMutation> {
  return apiRequest<AdminNotificationMutation>(
    `/v1/notifications/${encodeURIComponent(id)}`,
    { method: 'PUT', body: JSON.stringify({ active, verification_token: verificationToken }) }
  )
}

/* -------------------------------------------------------------------------- */
/* Role policies (read-only here)                                              */
/* -------------------------------------------------------------------------- */

export function fetchRolePolicies(): Promise<AdminRolePolicies> {
  return apiRequest<AdminRolePolicies>('/system/role-policies')
}

/* -------------------------------------------------------------------------- */
/* System settings                                                             */
/* -------------------------------------------------------------------------- */

export function fetchSystemSettings(): Promise<AdminSettings> {
  return apiRequest<AdminSettings>('/system/settings')
}

export interface SettingUpdateItem {
  key: string
  value: AdminSettingValue
}

export function saveSystemSettings(
  items: SettingUpdateItem[],
  verificationToken: string
): Promise<{ items?: AdminSettingItem[] }> {
  return apiRequest<{ items?: AdminSettingItem[] }>('/system/settings', {
    method: 'PUT',
    body: JSON.stringify({ items, verification_token: verificationToken })
  })
}

/* -------------------------------------------------------------------------- */
/* Logs                                                                        */
/* -------------------------------------------------------------------------- */

export function fetchLogs(
  page = 1,
  perpage = 10,
  query = ''
): Promise<ListResult<AdminLog[]>> {
  return apiList<AdminLog[]>(
    `/logs${buildQuery({ page, perpage, query })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Ban rules and records (read-only here)                                      */
/* -------------------------------------------------------------------------- */

export function fetchBanRules(): Promise<AdminBanRule[]> {
  return apiRequest<AdminBanRule[]>('/v1/ban/rules')
}

export function fetchBanRecords(
  page = 1,
  perpage = 10,
  query = ''
): Promise<ListResult<AdminBanRecord[]>> {
  return apiList<AdminBanRecord[]>(
    `/v1/ban/records${buildQuery({ page, perpage, query })}`
  )
}

/* -------------------------------------------------------------------------- */
/* Attachments (admin scope, read-only)                                        */
/* -------------------------------------------------------------------------- */

export function fetchAttachments(size = 20): Promise<ListResult<AdminAttachment[]>> {
  return apiList<AdminAttachment[]>(
    `/attachments${buildQuery({ scope: 'all', page: 1, size })}`
  )
}

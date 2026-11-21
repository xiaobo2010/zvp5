/**
 * 与后端真实契约对应的领域类型。
 * 字段名以后端实际响应为准（例如通知用 `id`，活动用 `_id`）。
 */

export type UserRole = 'student' | 'secretary' | 'department' | 'admin' | 'system' | 'auditor'

export interface UserProfile {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id?: string
  group?: string[]
  sex?: 'male' | 'female' | 'unknown'
  banned?: boolean
}

export interface LoginResult {
  token: string
  _id: string
  profile?: UserProfile
  weak_password?: boolean
  binding_mismatch?: boolean
  temp_session?: boolean
}

export type TimeMode = 'on-campus' | 'off-campus' | 'social-practice'

export type ActivityStatus = 'pending' | 'effective' | 'refused' | 'draft'

export type UserTime = Record<TimeMode, number>

export interface TimePercentile {
  value: number
  group: number
  grade: number
}

export type UserTimeStatistics = Record<TimeMode, TimePercentile>

export interface Activity {
  _id: string
  type: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  name: string
  description: string
  date: string
  createdAt: string
  updatedAt: string
  appointee: string
  approver: string
  creator: string
  creator_name?: string
  status: 'pending' | 'effective' | 'refused'
  place: string
  origin:
    | 'labor'
    | 'organization'
    | 'tasks'
    | 'occasions'
    | 'import'
    | 'activities'
    | 'practice'
    | 'club'
    | 'prize'
    | 'other'
    | 'unknown'
}

export interface ActivityMemberRow {
  _id: string
  activity: string
  member: string
  member_name: string
  mode: TimeMode
  status: 'effective' | 'pending' | 'refused'
  duration: number
  user?: UserProfile | null
}

export interface NotificationItem {
  id: string
  title: string
  content: string
  version: number
  content_hash?: string
  author_id?: string
  author_name?: string
  created_at?: string
  updated_at?: string
}

/** 管理端的通知行：多了启用状态，用户侧列表不会返回停用条目。 */
export interface AdminNotification extends NotificationItem {
  active?: boolean
  source_ref?: string | null
}

/** 审计日志行（`GET /v1/logs`）。timestamp 是秒级浮点。 */
export interface AuditLogRow {
  _id: string
  url: string
  user: string | null
  user_name?: string | null
  data: string
  timestamp: number
  type: string
  level: string
  xuehai_id?: string | null
  client_ip?: string | null
  user_agent?: string | null
}

/** 管理端用户行（`GET /v1/users`）：比 UserProfile 多一个班级名与封禁标记。 */
export interface AdminUserRow {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id: string
  class_name: string
  banned: boolean
}

/** 班级或权限组（`GET /v1/groups`）。权限组没有 type 字段时按 name+permissions 判断。 */
export interface GroupRow {
  _id: string
  name: string
  type?: 'class' | 'permission'
  description?: string
  permissions?: string[]
}

/** 班级 / 权限组成员行（`GET /v1/groups/{id}/users`）。 */
export interface GroupMemberRow {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id: string
  class_name: string
  banned: boolean
}

export interface FeaturePolicy {
  features: Record<string, boolean>
  quotas: {
    af: number | null
    schedule: number | null
  }
  unlimited: boolean
}

export interface ActivityPage {
  activities: Activity[]
  total: number
}

export interface UserActivityItem extends Activity {
  mine?: {
    _id: string
    member: string
    status: 'effective' | 'pending' | 'refused'
    mode: TimeMode
    duration: number
  }
}

export interface UserActivityPage {
  activities: UserActivityItem[]
  total: number
}

export interface MemberPage {
  members: ActivityMemberRow[]
  total: number
}

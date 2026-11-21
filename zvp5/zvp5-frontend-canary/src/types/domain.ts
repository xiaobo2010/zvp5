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

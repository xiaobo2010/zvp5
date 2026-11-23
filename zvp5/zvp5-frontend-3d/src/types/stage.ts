// Data contract between the API wiring (HomeView) and the 3D stage visuals.
//
// The stage owns presentation only: it receives a fully formatted snapshot and
// never fetches, so the same props can be replayed with fixtures or screenshots.
// Keep this file read-only for visual work — propose contract changes instead.

export type StageModuleId = 'identity' | 'hours' | 'activities' | 'extra'

export type StageTone = 'blue' | 'teal' | 'violet' | 'amber' | 'rose' | 'muted'

export interface StageModuleMeta {
  id: StageModuleId
  eyebrow: string
  title: string
  summary: string
}

export interface StageIdentity {
  name: string
  studentId: string
  className: string
  rolesLabel: string
  greeting: string
  dateLabel: string
  totalHours: number
  pendingCount: number
  /** Roles that unlock the fourth module's workbench content. */
  isAdmin: boolean
  isDepartment: boolean
}

export type StageHourKey = 'on-campus' | 'off-campus' | 'social-practice'

export interface StageHourPoint {
  key: StageHourKey
  label: string
  shortLabel: string
  hours: number
}

export interface StageHours {
  total: number
  points: StageHourPoint[]
}

export type StageActivityStatus = 'draft' | 'pending' | 'refused' | 'effective'

export interface StageActivity {
  id: string
  name: string
  dateLabel: string
  place: string
  typeLabel: string
  typeKey: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  status: StageActivityStatus
  statusLabel: string
  /** Hours credited to this member; null when the activity has no record yet. */
  hours: number | null
}

export type StageExtraKind = 'review' | 'agent' | 'log' | 'notice'

export interface StageExtraItem {
  id: string
  kind: StageExtraKind
  title: string
  meta: string
  detail: string
  tone: StageTone
}

export interface StageExtra {
  title: string
  subtitle: string
  items: StageExtraItem[]
  emptyLabel: string
}

export interface StageData {
  modules: StageModuleMeta[]
  identity: StageIdentity
  hours: StageHours
  activities: StageActivity[]
  extra: StageExtra
  /** Difference between the API server clock and the local clock, in ms. */
  serverOffsetMs: number
}

export const STAGE_MODULES: StageModuleMeta[] = [
  { id: 'identity', eyebrow: '01 / 概览', title: '简要信息', summary: '账号、班级与累计时长' },
  { id: 'hours', eyebrow: '02 / 时长', title: '义工时长', summary: '校内、校外与社会实践' },
  { id: 'activities', eyebrow: '03 / 记录', title: '最近义工', summary: '最近参与的活动与状态' },
  { id: 'extra', eyebrow: '04 / 工作台', title: '其他', summary: '学生会与管理员专属' }
]

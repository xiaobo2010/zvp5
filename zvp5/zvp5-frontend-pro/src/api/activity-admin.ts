import { apiRequest, apiVoid, buildQuery } from '@/api/http'

export type RegistrationMode = 'on-campus' | 'off-campus' | 'social-practice'

export interface AdminActivity {
  _id: string
  name: string
  type: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  date: string
  place?: string
  status?: 'pending' | 'effective' | 'refused' | 'draft'
  creator_name?: string
}

export interface ActivityMemberRow {
  _id: string
  member: string
  member_name?: string
  mode: RegistrationMode
  duration: number
  user?: {
    _id?: string
    id?: string | number
    name?: string
  }
}

export interface StudentRecord {
  _id: string
  id: string | number
  name: string
  group?: string[]
  class_id?: string
}

export interface ActivityMemberPage {
  members: ActivityMemberRow[]
  total: number
}

export async function listAdminActivities(query = '', page = 1, size = 50): Promise<AdminActivity[]> {
  const result = await apiRequest<{ activities?: AdminActivity[]; total?: number }>(
    `/v1/activities${buildQuery({ page, size, query })}`
  )
  return Array.isArray(result?.activities) ? result.activities : []
}

export async function listActivityMembers(
  activityId: string,
  query = '',
  page = 1,
  size = 100
): Promise<ActivityMemberPage> {
  const result = await apiRequest<ActivityMemberPage>(
    `/v1/activities/${activityId}/members${buildQuery({ page, size, query })}`
  )
  return {
    members: Array.isArray(result?.members) ? result.members : [],
    total: Number(result?.total) || 0
  }
}

export async function addActivityMembers(
  activityId: string,
  members: Array<{ member: string; mode: RegistrationMode; duration: number }>
): Promise<void> {
  await apiVoid(
    `/v1/activities/${activityId}/members${buildQuery({ reject_existing: true })}`,
    {
      method: 'POST',
      body: JSON.stringify(members)
    }
  )
}

export async function updateActivityMember(
  activityId: string,
  memberOid: string,
  duration: number,
  mode: RegistrationMode
): Promise<void> {
  await apiVoid(`/v1/activities/${activityId}/members/${memberOid}/record`, {
    method: 'PUT',
    body: JSON.stringify({ duration, mode })
  })
}

export async function searchStudents(query: string, page = 1, size = 10): Promise<StudentRecord[]> {
  const result = await apiRequest<{ data?: StudentRecord[]; total?: number } | StudentRecord[]>(
    `/v1/users${buildQuery({ page, size, query })}`
  )
  if (Array.isArray(result)) return result
  return Array.isArray(result?.data) ? result.data : []
}

export async function lookupStudent(value: string): Promise<StudentRecord | null> {
  const text = value.trim()
  if (!text) return null
  const rows = await searchStudents(text, 1, 10)
  const normalized = text.toLowerCase()
  return (
    rows.find((row) => String(row.id).toLowerCase() === normalized) ??
    rows.find((row) => row.name.trim() === text) ??
    null
  )
}

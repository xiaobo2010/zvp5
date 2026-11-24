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

/**
 * 学号/姓名查询的结果。
 *
 * 刻意区分「查无此人」「同名多人」「查询失败」：调用方需要给用户不同的提示，
 * 也需要知道 `matched` 之外的情况不能提交 oid。
 */
export type StudentLookupResult =
  | { status: 'matched'; student: StudentRecord }
  | { status: 'not-found' }
  | { status: 'ambiguous'; options: string[] }

export interface ActivityMemberPage {
  members: ActivityMemberRow[]
  total: number
}

export async function listAdminActivities(query = '', page = 1, size = 50): Promise<AdminActivity[]> {
  // `get_activities_v2` has no route decorator in the current backend, so
  // `GET /api/v1/activities` is the v3 list: page/size/query (not perpage/search).
  const result = await apiRequest<{ activities?: AdminActivity[]; total?: number }>(
    `/v1/activities${buildQuery({
      page,
      size: Math.min(Math.max(size, 1), 1000),
      query,
      sort: '_id',
      asc: false
    })}`
  )
  return Array.isArray(result?.activities) ? result.activities : []
}

export async function listActivityMembers(
  activityId: string,
  query = '',
  page = 1,
  size = 100
): Promise<ActivityMemberPage> {
  // The v3 endpoint caps `size` at 100 (422 above that).
  const result = await apiRequest<ActivityMemberPage>(
    `/v1/activities/${activityId}/members${buildQuery({
      page,
      size: Math.min(Math.max(size, 1), 100),
      query
    })}`
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
  memberDocumentId: string,
  duration: number,
  mode: RegistrationMode
): Promise<void> {
  // 第二个参数必须是活动成员**记录**的 `_id`（v3 成员列表里的 `_id`），不是成员
  // 的用户 oid：v2 的 `PUT /{activity_id}/members/{document_id}/record` 先校验
  // 记录归属再改时长，传用户 oid 会稳定 404。
  await apiVoid(`/v1/activities/${activityId}/members/${memberDocumentId}/record`, {
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

export async function lookupStudent(value: string): Promise<StudentLookupResult> {
  const text = value.trim()
  if (!text) return { status: 'not-found' }
  // `GET /api/v1/users` 按 `name`/`business_id` 做包含匹配，排序键是学号，
  // 所以「张伟」的精确行可能排在一堆「张伟X」之后。默认每页 10 条会让精确匹配
  // 落在页外、被误判成查无此人；这里一次拿满一页（上限 100）再在本地挑精确项。
  const rows = await searchStudents(text, 1, 100)
  const normalized = text.toLowerCase()
  const byId = rows.find((row) => String(row.id).toLowerCase() === normalized)
  if (byId) return { status: 'matched', student: byId }
  const byName = rows.filter((row) => row.name.trim() === text)
  if (byName.length === 1 && byName[0]) return { status: 'matched', student: byName[0] }
  if (byName.length > 1) {
    // 同名同姓不算错误输入，但不该让前端随便挑一个：把候选学号交给用户决定。
    return { status: 'ambiguous', options: byName.map((row) => String(row.id)).slice(0, 3) }
  }
  return { status: 'not-found' }
}

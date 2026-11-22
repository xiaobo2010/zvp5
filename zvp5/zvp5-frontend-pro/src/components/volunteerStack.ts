import type { CanaryActivity } from '@/api/canary'
import { ref, watch } from 'vue'

export function useVolunteerStackExpansion(getRows: () => Activity[], getContext: () => string) {
  const expandedId = ref<string | null>(null)
  watch(
    [getContext, () => JSON.stringify(getRows().map((activity) => activity.rowKey))],
    () => { expandedId.value = getRows().at(-1)?.rowKey ?? null },
    { immediate: true }
  )
  return expandedId
}

export type ActivityStatus = 'draft' | 'pending' | 'refused' | 'effective'
type ActivityType = CanaryActivity['type']

export interface Activity {
  rowKey: string
  id: string
  name: string
  detailDate: string
  place: string
  type: string
  typeClass: string
  status: ActivityStatus
  statusLabel: string
  statusClass: string
  duration: number
  hasDuration: boolean
  creator: string
  memberStatus: ActivityStatus | null
}

const TYPE_LABELS: Record<ActivityType, string> = {
  'on-campus': '校内活动',
  'off-campus': '校外活动',
  'social-practice': '社会实践',
  hybrid: '混合活动'
}

function normalizeStatus(value: string | undefined): ActivityStatus {
  if (value === 'draft' || value === 'refused' || value === 'effective') {
    return value
  }
  return 'pending'
}

export function mapActivity(activity: CanaryActivity): Activity {
  const mine = activity.mine
  const status = normalizeStatus(activity.status)
  const date = activity.date || ''
  return {
    rowKey: mine?._id || activity._id,
    id: activity._id,
    name: activity.name || '未命名活动',
    detailDate: formatDetailDate(date),
    place: activity.place || '',
    type: TYPE_LABELS[mine?.mode ?? activity.type] ?? '活动',
    typeClass: `activity-type--${mine?.mode ?? activity.type}`,
    status,
    statusLabel: getStatusLabel(status),
    statusClass: getStatusClass(status),
    duration: typeof mine?.duration === 'number' ? mine.duration : 0,
    hasDuration: typeof mine?.duration === 'number',
    creator: activity.creator_name || '',
    memberStatus: mine ? normalizeStatus(mine.status) : null
  }
}

function formatDetailDate(date: string) {
  const match = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(date)
  if (match) return match[2] ? `${match[1]} ${match[2]}` : match[1]
  return date || '—'
}

export function getStatusLabel(status: ActivityStatus) {
  const labels: Record<ActivityStatus, string> = {
    draft: '草稿',
    pending: '待审核',
    refused: '已拒绝',
    effective: '已生效'
  }
  return labels[status]
}

export function getStatusClass(status: ActivityStatus) {
  const classes: Record<ActivityStatus, string> = {
    draft: 'status-draft',
    pending: 'status-pending',
    refused: 'status-refused',
    effective: 'status-effective'
  }
  return classes[status]
}

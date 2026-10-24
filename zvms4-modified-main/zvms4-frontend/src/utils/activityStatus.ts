import type { Activity, ActivityMember } from '@/../types/v2'

export type ActivityDisplayStatus = Activity['status'] | ActivityMember['status']

export interface ActivityStatusSource {
  status?: Activity['status'] | null
  mine?: { status?: ActivityMember['status'] | null } | null
}

const MEMBER_STATUSES: readonly ActivityMember['status'][] = ['effective', 'pending', 'refused']

/**
 * @description The status shown for one activity row of a user.
 *
 * The accumulated volunteer time counts a row only when both the activity and
 * the membership are effective, so the activity status wins. Missing or unknown
 * data falls back to `pending`: telling the user a row is not counted yet is
 * safer than claiming it passed review.
 */
export function displayStatus(
  row: ActivityStatusSource | null | undefined
): ActivityDisplayStatus {
  const activityStatus = row?.status

  if (activityStatus === 'pending' || activityStatus === 'refused') {
    return activityStatus
  }

  if (activityStatus !== 'effective') {
    return 'pending'
  }

  const memberStatus = row?.mine?.status

  if (memberStatus && MEMBER_STATUSES.includes(memberStatus)) {
    return memberStatus
  }

  return 'pending'
}

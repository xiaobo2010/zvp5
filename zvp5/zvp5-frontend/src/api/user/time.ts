import axios from '@/plugins/axios'
import type { UserActivityTimeSums } from '@/../types'
import type { ActivityMember } from '@/../types/activity.v2'

async function getUserTime(user: string) {
  return (await axios(`/v1/users/${user}/time`)).data as UserActivityTimeSums
}

export interface UserActivityTimePercentile {
  value: number
  group: number
  grade: number
}

async function getUserTimePercentile(user: string) {
  return (await axios(`/v1/users/${user}/time_statistics`)).data as Record<
    ActivityMember['mode'],
    UserActivityTimePercentile
  >
}

export { getUserTime as read, getUserTimePercentile as percentile }

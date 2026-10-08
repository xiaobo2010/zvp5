import axios from '@/plugins/axios'
import type { Activity, ActivityMember } from '@/types/activity.v2'

async function getUserActivities(
  id: string,
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  sort: string = '_id',
  ascending: boolean = false
) {
  return (
    await axios(`/v2/users/${id}/activities`, {
      method: 'get',
      params: {
        page,
        perpage,
        search,
        sort,
        asc: ascending
      }
    })
  ).data as {
    activities: Activity[]
    total: number
  }
}

async function getUserActivitiesV3(
    id: string,
    page: number = 1,
    size: number = 10,
    query: string = ''
) {
    return (
        await axios(`/v3/users/${id}/activities`, {
            method: 'GET',
            params: {
                page, size, query
            }
        })
    ).data as {
        activities: Array<Activity & {
            mine: ActivityMember
        }>
        total: number
    }
}

export { getUserActivities as read, getUserActivitiesV3 as read_v3 }

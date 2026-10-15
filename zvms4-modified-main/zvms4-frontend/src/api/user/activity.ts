import axios from '@/plugins/axios'
import type { ActivityInstance, Response } from '@/../types'
import { ElNotification } from 'element-plus'
import type { ActivityMember, Activity } from '../../../types/activity.v2'

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
    activities: (Activity & {
      mine?: {
        _id: string
        member: string
        status: ActivityMember['status']
        mode: ActivityMember['mode']
        duration: number
      }
    })[]
    total: number
  }
}

export { getUserActivities as read }

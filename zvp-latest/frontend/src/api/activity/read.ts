import axios from '@/plugins/axios'
import type { Activity } from '@/types/v2'
import { read as mine, read_v3 as mine_v3 } from '@/api/user/activity'

async function getClassActivities(
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  classid: string = '',
  sort: string = '_id',
  asc: boolean = false
) {
  return (
    await axios(`/v2/groups/${classid}/activities`, {
      params: {
        page,
        perpage,
        search,
        classid,
        sort,
        asc
      }
    })
  ).data as {
    activities: Activity[]
    total: number
  }
}

async function getClassActivitiesV3(
    class_id: string,
    page: number,
    pageSize: number,
    query: string = ''
) {
    return (
        await axios(`/v3/groups/${class_id}/activities`, {
            method: 'GET',
            params: {
                page, size: pageSize, query
            }
        })
    ).data as {
        activities: Activity[]
        total: number
    }
}

async function getAllActivitiesV3(
    page: number,
    pageSize: number,
    query: string = '',
    {
        type = '',
        status = ''
    } = {}
) {
    return (
        await axios('/v3/activities', {
            params: {
                page, size: pageSize, query, type, status
            }
        })
    ).data as {
        activities: Activity[]
        total: number
    }
}

async function getActivity(id: string) {
  // id: ObjectId
  return (await axios(`/v2/activities/${id}`)).data as {
    activity: Activity
    members_count: number
  }
}

const exports = {
  campus_v3: getAllActivitiesV3,
  class: (
    page: number = 1,
    perpage: number = 10,
    query: string = '',
    classid: string = '',
    sortField: string = '_id',
    ascending: boolean = false
  ) => getClassActivities(page, perpage, query, classid, sortField, ascending),
  class_v3: getClassActivitiesV3,
  mine,
  mine_v3,
  single: (id: string) => getActivity(id)
}

export { exports as read }

import axios from '@/plugins/axios'
import type { Activity } from '@/../types/v2'
import { read as mine } from '@/api/user/activity'

async function getClassActivities(
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  classid: string = '',
  sort: string = '_id',
  asc: boolean = false
) {
  return (
    await axios(`/v1/groups/${classid}/activities`, {
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

async function getAllActivities(
  filter: {
    type: string
  },
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  sortField: string = '_id',
  ascending: boolean = false
) {
  // v5 起 /api/v1/activities 的列表由 v3 实现接管（旧 v2 列表装饰器已摘除），
  // 分页与筛选参数名相应改为 size/query/type。
  return (
    await axios('/v1/activities', {
      params: {
        type: filter.type === 'all' ? '' : filter.type,
        page,
        size: perpage,
        query: search,
        sort: sortField,
        asc: ascending
      }
    })
  ).data as {
    activities: Activity[]
    total: number
  }
}

async function getActivity(id: string) {
  // id: ObjectId
  return (await axios(`/v1/activities/${id}`)).data as {
    activity: Activity
    members_count: number
  }
}

async function getAllActivitiesV3(
  page: number = 1,
  size: number = 10,
  query: string = '',
  type: string = '',
  status: string = ''
) {
  return (
    await axios('/v1/activities', {
      method: 'GET',
      params: {
        page,
        size,
        query,
        type,
        status
      }
    })
  ).data as {
    total: number
    activities: (Activity & { creator_name: string })[]
  }
}

const exports = {
  campus: (
    filter: string,
    page: number = 1,
    perpage: number = 10,
    query: string = '',
    sortField: string = '_id',
    ascending: boolean = false
  ) => getAllActivities({ type: filter }, page, perpage, query, sortField, ascending),
  campus_v3: getAllActivitiesV3,
  class: (
    page: number = 1,
    perpage: number = 10,
    query: string = '',
    classid: string = '',
    sortField: string = '_id',
    ascending: boolean = false
  ) => getClassActivities(page, perpage, query, classid, sortField, ascending),
  mine,
  single: (id: string) => getActivity(id)
}

export { exports as read }

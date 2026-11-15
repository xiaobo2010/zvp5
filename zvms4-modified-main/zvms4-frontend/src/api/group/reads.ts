import axios from '@/plugins/axios'
import type { User, ActivityTimePage } from '@/../types'

export async function users(
  gid: string,
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  pwdm: boolean = false
) {
  // v5 起 /api/v1/groups/{id}/users 只有 v3 实现（v2 的 GET 装饰器已摘除），
  // 参数名是 size/query，响应是 { data, total }。
  return users_v3(gid, page, perpage, search, pwdm)
}

export async function users_v3(
  gid: string,
  page: number = 1,
  size: number = 10,
  query: string = '',
  pwdm: boolean = false
) {
  const response = (
    await axios(`/v1/groups/${gid}/users`, {
      params: {
        page,
        size,
        query,
        pwdm
      }
    })
  ).data as {
    data: User[]
    total: number
  }
  return {
    users: response.data,
    size: response.total
  }
}

export async function time(
  gid: string,
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  exceed: boolean = true,
  shortage: boolean = false,
  sort: string = 'id',
  asc: boolean = true
) {
  const response = (
    await axios(`/v1/groups/${gid}/time`, {
      params: {
        page,
        perpage,
        exceed,
        shortage,
        search,
        sort,
        asc
      }
    })
  ).data as ActivityTimePage
  return {
    time: response.data,
    size: response.metadata.size
  }
}

export async function statisticsCompliance(groupId: string) {
  return (await axios(`/v1/groups/${groupId}/statistics/compliance`)).data
}

import axios from '@/plugins/axios'
import type { User, GroupUsersPage, ActivityTimePage } from '@/../types'

export async function users(
  gid: string,
  page: number = 1,
  perpage: number = 10,
  search: string = '',
  pwdm: boolean = false
) {
  const response = (
    await axios(`/v2/groups/${gid}/users`, {
      params: {
        page,
        perpage,
        search,
        pwdm
      }
    })
  ).data as GroupUsersPage
  return {
    users: response.members,
    size: response.total
  }
}

export async function users_v3(
  gid: string,
  page: number = 1,
  size: number = 10,
  query: string = '',
  pwdm: boolean = false
) {
  const response = (
    await axios(`/v3/groups/${gid}/users`, {
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
    await axios(`/v2/groups/${gid}/time`, {
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
  return (await axios(`/v2/groups/${groupId}/statistics/compliance`)).data
}

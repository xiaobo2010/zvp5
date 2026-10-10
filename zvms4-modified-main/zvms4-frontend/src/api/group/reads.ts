import axios from '@/plugins/axios'
import type { Response, User } from '@/../types'

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
  ).data as {
    members: User[]
    total: number
  }
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
  ).data as {
    data: {
      _id: string
      name: string
      id: string
      'on-campus': number
      'off-campus': number
      'social-practice': number
    }[]
    metadata: { size: number }
  }
  return {
    time: response.data,
    size: response.metadata.size
  }
}

export async function statisticsCompliance(groupId: string) {
  return (await axios(`/v2/groups/${groupId}/statistics/compliance`)).data
}

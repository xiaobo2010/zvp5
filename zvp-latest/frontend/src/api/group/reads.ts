import axios from '@/plugins/axios'
import type { Response, User } from '@/types'

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
    id: string,
    {
        page,
        pageSize,
        query
    }
) {
    return (
        await axios(`/v3/groups/${id}/users`, {
            method: 'GET',
            params: {
                page, size: pageSize, query
            }
        })
    ).data
}

export async function time(
    gid: string,
    params: {
        page?: number
        perpage?: number
        query?: string
        sort?: string
        asc?: boolean
    } = {}
) {
    return (
        await axios(`/v2/groups/${gid}/time`, {
            method: 'GET',
            params
        })
    ).data as {
        data: Array<{
            _id: string
            id: string
            name: string
            'on-campus': number
            'off-campus': number
            'social-practice': number
        }>
        metadata: {
            size: number
        }
    }
}


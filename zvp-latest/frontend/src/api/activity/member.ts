import axios from '@/plugins/axios'
import { temporaryToken } from '@/api/user/temp-token'
import type { Response } from '@/types'
import type { ActivityMember, NewActivityMember } from '@/types/v2'

export async function insert(aid: string, member: ActivityMember) {
  const result = (
    await axios(`/v2/activities/${aid}/members`, {
      method: 'post',
      data: member
    })
  ).data as Response<ActivityMember>
    return result.data
}

export async function insertMany(aid: string, members: NewActivityMember[]) {
  await axios(`/v3/activities/${aid}/members`, {
    method: 'post',
    data: members
  })
}

export async function remove(id: string, aid: string, uid: string) {
  const token = await temporaryToken(uid)
  const result = (
    await axios(`/v2/activities/${aid}/members/${id}`, {
      method: 'delete',
      headers: {
        Authorization: `Bearer ${token}`
      }
    })
  ).data as Response<ActivityMember>
    return result.data
}

export async function read(aid: string, sid: string) {
  return (await axios(`/v2/activities/${aid}/members/${sid}`)).data as ActivityMember
}

export async function reads(aid: string, page: number, perpage: number, search: string) {
  const result = (
    await axios(`/v2/activities/${aid}/members`, {
      params: {
        page,
        perpage,
        search
      }
    })
  ).data as {
    members: ActivityMember[]
    total: number
  }
  return result
}

export async function reads_v3(aid: string, page: number, pageSize: number, query: string) {
    return (
        await axios(`/v3/activities/${aid}/members`, {
            method: 'GET',
            params: { page, size: pageSize, query }
        })
    ).data as {
        members: ActivityMember[]
        total: number
    }
}

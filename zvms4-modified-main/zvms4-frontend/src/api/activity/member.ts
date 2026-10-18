import axios from '@/plugins/axios'
import { temporaryToken } from '@/plugins/short-token'
import type { Response } from '@/../types'
import type { ActivityMember, NewActivityMember } from '@/../types/v2'

export async function insert(aid: string, member: ActivityMember) {
  const result = (
    await axios(`/v2/activities/${aid}/members`, {
      method: 'post',
      data: member
    })
  ).data as Response<ActivityMember>
  if (result.status === 'ok') {
    return result.data
  }
}

export async function insertMany(aid: string, members: NewActivityMember[]): Promise<void> {
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
  ).data as { detail?: unknown }
  // 服务端成功响应是 {"detail": "..."}，不是 v1 的 {status:'ok',data}。
  // 形状不符即视为失败并抛出，避免静默 no-op。
  if (typeof result?.detail !== 'string') {
    throw new Error('移除活动成员失败：服务端未确认删除')
  }
  return result
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

export async function reads_v3(aid: string, page: number, size: number, query: string) {
  return (
    await axios(`/v3/activities/${aid}/members`, {
      method: 'GET',
      params: {
        page,
        size,
        query
      }
    })
  ).data as {
    members: (ActivityMember & { member_name: string })[]
    total: number
  }
}

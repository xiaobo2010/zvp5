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

/**
 * 批量新增活动成员。
 *
 * `options.overwrite` 为 true 时带 `?overwrite=true` 查询参数，后端据此把同
 * (成员, 模式) 的时长改成「设置」而不是「累加」。后端尚未上线该参数时查询参数
 * 会被忽略，行为与原来完全一致，因此前端可以先于后端发布。
 *
 * `options.timeout` 覆盖 axios 实例默认的 12s：批量写入 + 大活动时后端要跑事务，
 * 用默认超时会被中途掐断。
 */
export async function insertMany(
  aid: string,
  members: NewActivityMember[],
  options: { overwrite?: boolean; timeout?: number } = {}
): Promise<void> {
  await axios(`/v3/activities/${aid}/members`, {
    method: 'post',
    data: members,
    params: options.overwrite ? { overwrite: true } : undefined,
    timeout: options.timeout
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

export async function modifyStatus(
  aid: string,
  sessionId: string,
  status: ActivityMember['status']
) {
  return (
    await axios({
      url: `/v2/activities/${aid}/members/${sessionId}/status`,
      method: 'put',
      data: {
        status
      }
    })
  ).data as {
    id: string
    status: ActivityMember['status']
  }
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

export async function reads_v3(
  aid: string,
  page: number,
  size: number,
  query: string,
  options: { timeout?: number } = {}
) {
  return (
    await axios(`/v3/activities/${aid}/members`, {
      method: 'GET',
      params: {
        page,
        size,
        query
      },
      timeout: options.timeout
    })
  ).data as {
    members: (ActivityMember & { member_name: string })[]
    total: number
  }
}

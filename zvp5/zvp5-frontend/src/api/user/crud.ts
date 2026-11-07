import { temporaryToken } from '@/plugins/short-token'
import type { User, Response } from '@/../types'
import { ElNotification } from 'element-plus'
import axios from '@/plugins/axios.ts'
import { translateKey } from '@/utils/errorMessage'

async function getUser(id: string, overrideAxios: boolean = true) {
  const result = (
    await axios(`/users/${id}`, overrideAxios ? { timeout: 24000 } : undefined)
  ).data as Response<User>
  if (result.status === 'error') {
    throw result
  }
  return result?.data as User
}

async function getUsers(id: string = '', page: number = 1, perpage: number = 5) {
  const result = (
    await axios(`/users`, {
      params: {
        query: id,
        page,
        perpage
      }
    })
  ).data as Response<User[]> & {
    metadata: {
      size: number
    }
  }
  if (result.status === 'error') {
    ElNotification({
      title: translateKey('common.fetchFailed', { code: result.code }),
      message: result.message,
      type: 'error'
    })
    return
  }
  return {
    users: result.data,
    size: result.metadata.size
  }
}

async function putUser(
  user: string,
  target: string,
  name: string,
  id: string,
  group: string[],
  create: boolean
) {
  const token = await temporaryToken(user)
  const result = (
    await axios({
      method: create ? 'post' : 'put',
      url: create ? '/users' : `/users/${target}`,
      data: { name, group, id, sex: 'unknown', past: [] },
      headers: {
        Authorization: 'Bearer ' + token
      }
    })
  ).data as Response<null>
  if (result.status === 'error') {
    ElNotification({
      title: translateKey('common.userUpdateFailed', { code: result.code }),
      message: result.message,
      type: 'error'
    })
    return false
  }
  return true
}

export const past = {
  async insert(user: string, past: string) {
    const result = (
      await axios({
        method: 'post',
        url: `/users/${user}/past`,
        data: { past }
      })
    ).data as Response<null>
    if (result.status === 'error') {
      ElNotification({
        title: translateKey('common.userUpdateFailed', { code: result.code }),
        message: result.message,
        type: 'error'
      })
      return false
    }
    return true
  },
  async delete(user: string, idx: number) {
    const result = (
      await axios({
        method: 'delete',
        url: `/users/${user}/past/${idx}`
      })
    ).data as Response<null>
    if (result.status === 'error') {
      ElNotification({
        title: translateKey('common.userUpdateFailed', { code: result.code }),
        message: result.message,
        type: 'error'
      })
      return false
    }
    return true
  }
}

async function getUsersV2(
  page: number = 1,
  size: number = 10,
  query: string = '',
  role: string = ''
) {
  const result = (
    await axios('/v1/users', {
      params: { page, size, query, role }
    })
  ).data as { data: User[]; total: number }
  return {
    users: result.data,
    size: result.total
  }
}

async function getUserV3(id: string) {
  const result = (await axios(`/v1/users/${id}`)).data as Response<User>
  if (result.status === 'error') {
    throw result
  }
  return result.data as User
}

async function putUserRoles(uid: string, roles: string[]) {
  const response = await axios({
    method: 'put',
    url: `/v1/users/${uid}/roles`,
    data: { roles }
  })
  return response?.status === 204
}

async function putUserStudentId(uid: string, studentId: string) {
  const response = await axios({
    method: 'put',
    url: `/v1/users/${uid}/student-id`,
    data: { id: studentId }
  })
  return response?.status >= 200 && response?.status < 300
}

async function deleteUserV3(uid: string, verificationToken?: string) {
  const response = await axios({
    method: 'delete',
    url: `/v1/users/${uid}`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
  return response?.status >= 200 && response?.status < 300
}

async function putUserBanned(
  uid: string,
  banned: boolean,
  reason?: string,
  durationDays?: number | null
) {
  const data: Record<string, unknown> = { banned }
  // 解封没有原因语义;封禁时只在真的填了原因才带 reason,避免把空串当"已填写"传下去,
  // 让后端自己回退到默认原因。
  if (banned && reason) data.reason = reason
  // 时长只传天数(服务端按本地时区算到期时刻):null/undefined = 永久,字段直接省略,
  // 老后端收到没有 duration_days 的 body 仍是永久封禁,兼容旧契约。
  if (banned && durationDays != null) data.duration_days = durationDays
  const response = await axios({
    method: 'put',
    url: `/v1/users/${uid}/banned`,
    data
  })
  // 200 带 ban_until 响应体(便于提示到期时间);保留对 204 的兼容。
  return response?.status >= 200 && response?.status < 300
}

async function createUserV3(
  id: string,
  name: string,
  class_id: string = '',
  roles: string[] = ['student']
) {
  const result = (
    await axios({
      method: 'post',
      url: '/v1/users',
      data: { id, name, roles, class_id: class_id || undefined }
    })
  ).data as { id: string; initial_password?: string }
  if (!result?.id) return false
  // 一次性初始密码只在本次响应中出现；旧契约尚无该字段时为空串
  return { id: result.id, initial_password: result.initial_password ?? '' }
}

export { getUser as readOne, getUsers as read, putUser as update, getUserV3 as readOne_v3, getUsersV2 as read_v2, putUserRoles as updateRoles, putUserStudentId as updateStudentId, deleteUserV3 as deleteUser, putUserBanned as updateBanned, createUserV3 as create_v3 }

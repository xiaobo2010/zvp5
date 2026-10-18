import { temporaryToken } from '@/plugins/short-token'
import type { User, Response } from '@/../types'
import { ElNotification } from 'element-plus'
import axios from '@/plugins/axios.ts'

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
      title: 'Error when fetching data (' + result.code + ')',
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
      title: 'Error when updating user: ' + result.message,
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
        title: 'Error when updating user: ' + result.message,
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
        title: 'Error when updating user: ' + result.message,
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
    await axios('/v2/users', {
      params: { page, size, query, role }
    })
  ).data as { data: User[]; total: number }
  return {
    users: result.data,
    size: result.total
  }
}

async function getUserV3(id: string) {
  const result = (await axios(`/v3/users/${id}`)).data as Response<User>
  if (result.status === 'error') {
    throw result
  }
  return result.data as User
}

async function putUserRoles(uid: string, roles: string[]) {
  const response = await axios({
    method: 'put',
    url: `/v3/users/${uid}/roles`,
    data: { roles }
  })
  return response?.status === 204
}

async function putUserStudentId(uid: string, studentId: string) {
  const response = await axios({
    method: 'put',
    url: `/v3/users/${uid}/student-id`,
    data: { id: studentId }
  })
  return response?.status >= 200 && response?.status < 300
}

async function deleteUserV3(uid: string) {
  const response = await axios({
    method: 'delete',
    url: `/v3/users/${uid}`
  })
  return response?.status >= 200 && response?.status < 300
}

async function putUserBanned(uid: string, banned: boolean) {
  const response = await axios({
    method: 'put',
    url: `/v3/users/${uid}/banned`,
    data: { banned }
  })
  return response?.status === 204
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
      url: '/v3/users',
      data: { id, name, roles, class_id: class_id || undefined }
    })
  ).data as { id: string; initial_password?: string }
  if (!result?.id) return false
  // 一次性初始密码只在本次响应中出现；旧契约尚无该字段时为空串
  return { id: result.id, initial_password: result.initial_password ?? '' }
}

export { getUser as readOne, getUsers as read, putUser as update, getUserV3 as readOne_v3, getUsersV2 as read_v2, putUserRoles as updateRoles, putUserStudentId as updateStudentId, deleteUserV3 as deleteUser, putUserBanned as updateBanned, createUserV3 as create_v3 }

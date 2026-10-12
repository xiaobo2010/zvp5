import { temporaryToken } from '@/plugins/short-token'
import type { User, Response } from '@/../types'
import { ElNotification } from 'element-plus'
import nprogress from 'nprogress'
import axiosExported, { AxiosError } from 'axios'
import { baseURL, default as axios } from '@/plugins/axios.ts'

async function getUser(id: string, overrideAxios: boolean = true) {
  const instance = axiosExported.create({
    baseURL,
    withCredentials: true,
    timeout: 24000,
    headers: {
      'Content-type': 'application/json',
      Authorization: localStorage.getItem('token') ? `Bearer ${localStorage.getItem('token')}` : ''
    }
  })
  instance.interceptors.response.clear()
  instance.interceptors.response.use(
    (response) => {
      nprogress.done()
      return response.data
    },
    (error: Error | AxiosError) => {
      nprogress.done()
      console.log(error, 'error')
      return {
        status: 'error',
        code: (error instanceof AxiosError ? error.response?.status : 500) ?? 500,
        message: error.message
      }
    }
  )
  const axiosInstance = overrideAxios ? instance : axios
  const result = (await axiosInstance(`/users/${id}`)) as Response<User>
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
  ).data as { id: string }
  return result?.id ?? false
}

export { getUser as readOne, getUsers as read, putUser as update, getUserV3 as readOne_v3, getUsersV2 as read_v2, putUserRoles as updateRoles, createUserV3 as create_v3 }

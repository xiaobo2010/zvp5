import { temporaryToken } from '@/api/user/temp-token'
import type { User, Response } from '@/types'
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
  const result = (await axiosInstance(`/users/${id}`))
  if (result?.status === 'error') {
    throw result
  }
  return result?.data
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
  return {
    users: result.data,
    size: result.metadata.size
  }
}

async function getUsersV2({
    page, pageSize, query, ...extra
}) {
    const result = (
        await axios('/v2/users', {
            method: 'GET',
            params: { page, size: pageSize, query, ...extra }
        })
    ).data as {
        data: Array<{
            _id: string
            id: string
            name: string
            roles: string[]
            class_id: string
            class_name?: string
        }>
        total: number
    }
    return result
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
    await axios({
      method: create ? 'post' : 'put',
      url: create ? '/users' : `/users/${target}`,
      data: { name, group, id, sex: 'unknown', past: [] },
      headers: {
        Authorization: 'Bearer ' + token
      }
    })
  return true
}

export const past = {
  async delete(user: string, idx: number) {
      await axios({
        method: 'delete',
        url: `/users/${user}/past/${idx}`
      })
    return true
  }
}

async function deleteUser(user: string, target: string) {
  const token = await temporaryToken(user)
    await axios({
      method: 'delete',
      url: `/users/${target}`,
      data: { target },
      headers: {
        Authorization: 'Bearer ' + token
      }
    })
  return true
}

async function putUserRoles(uid: string, roles: string[]) {
  await axios(`/v3/users/${uid}/roles`, {
    method: 'put',
    data: { roles }
  })
  return true
}

export { getUser as readOne, getUsers as read, getUsersV2 as read_v2, putUser as update, deleteUser as removeOne, putUserRoles as updateRoles }

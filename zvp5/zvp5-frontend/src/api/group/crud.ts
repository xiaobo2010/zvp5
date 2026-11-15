import axios from '@/plugins/axios'
import { temporaryToken } from '@/plugins/short-token'
import type { Group, Response, UserPosition } from '@/../types'
import { ElNotification } from 'element-plus/es/components/notification/index.mjs'

export async function createGroup(group: Group) {
  const response = await axios({
    method: 'post',
    url: '/groups',
    data: group
  })
  const data = response.data as Response<{ _id: string }>
  if (data.status === 'ok') {
    ElNotification({
      title: 'Success',
      message: 'Group created successfully: ' + data.data._id,
      type: 'success'
    })
    return data.data._id
  }
  return false
}

export async function getGroup(gid: string) {
  const response = await axios('/groups/' + gid)
  const data = response.data as Response<Group>
  if (data.status === 'ok') {
    return data.data
  }
}

export async function getGroups(
  type: 'all' | 'permission' | 'class' = 'all',
  page = 1,
  limit = 10,
  search = ''
) {
  const response = await axios('/groups', {
    params: { type, page, perpage: limit, search }
  })
  const data = response.data as Response<Group[]> & { metadata: { size: number } }
  if (data.status === 'ok') {
    return {
      groups: data.data,
      size: data.metadata.size
    }
  }
}

export async function editGroupName(gid: string, name: string) {
  const response = await axios({
    method: 'put',
    url: `/groups/${gid}/name`,
    data: { name }
  })
  const data = response.data as Response<null>
  return data.status === 'ok'
}

export async function editGroupDescription(gid: string, description: string) {
  const response = await axios({
    method: 'put',
    url: `/groups/${gid}/description`,
    data: { description }
  })
  const data = response.data as Response<null>
  return data.status === 'ok'
}

export async function deleteGroup(uid: string, gid: string) {
  const token = await temporaryToken(uid)
  const response = await axios({
    method: 'delete',
    url: `/v1/groups/${gid}`,
    headers: {
      Authorization: `Bearer ${token}`
    }
  })
  return response?.status === 204
}

export async function getGroupsV2(
  page: number = 1,
  size: number = 10,
  query: string = ''
) {
  // The backend caps a page at 100 rows. Clamp at the API boundary so an old
  // caller cannot turn a routine list request into a 422 response.
  const safePage = Number.isFinite(page) && page >= 1 ? Math.trunc(page) : 1
  const safeSize = Number.isFinite(size)
    ? Math.min(100, Math.max(1, Math.trunc(size)))
    : 10
  const response = (
    await axios('/v1/groups', {
      params: { page: safePage, size: safeSize, query }
    })
  ).data as { data: Group[]; total: number }
  return {
    groups: response.data,
    size: response.total
  }
}

export async function createGroupV3(name: string) {
  const response = (
    await axios({
      method: 'post',
      url: '/v1/groups',
      data: { name }
    })
  ).data as { id: string }
  if (response?.id) {
    return response.id
  }
  return false
}

export async function addGroupUserV3(gid: string, uid: string) {
  const response = await axios({
    method: 'post',
    url: `/v1/groups/${gid}/users/${uid}`
  })
  return response?.status === 204
}

export async function removeGroupUserV3(gid: string, uid: string) {
  const response = await axios({
    method: 'delete',
    url: `/v1/groups/${gid}/users/${uid}`
  })
  return response?.status === 204
}

export const updateMethods = {
  name: editGroupName,
  description: editGroupDescription,
}

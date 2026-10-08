import axios from '@/plugins/axios'
import { temporaryToken } from '@/api/user/temp-token'
import type { Group, Response, UserPosition } from '@/types'

export async function createGroup(group: Group) {
  const response = await axios({
    method: 'post',
    url: '/groups',
    data: group
  })
  const data = response.data as Response<{ _id: string }>
    return data.data._id
}

export async function getGroupV2(gid: string) {
    return (
        await axios('/v2/groups/' + gid, {
            method: 'GET'
        })
    ).data
}

export async function getGroupsV2(
    page = 1,
    pageSize = 10,
    query = ''
) {
    return (
        await axios('/v2/groups', {
            method: 'GET',
            params: {
                page, size: pageSize, query
            }
        })
    ).data as {
        data: Array<{
            _id: string
            name: string
        }>
        total: number
    }
}

export async function editGroupName(gid: string, name: string) {
  await axios({
    method: 'put',
    url: `/groups/${gid}/name`,
    data: { name }
  })
  return true
}

export async function deleteGroup(uid: string, gid: string) {
  const token = await temporaryToken(uid)
  await axios({
    method: 'delete',
    url: `/groups/${gid}`,
    headers: {
      Authorization: `Bearer ${token}`
    }
  })
  return true
}

export const updateMethods = {
  name: editGroupName,
}

export async function createGroupV3(name: string, enrollYear?: number) {
  return (
    await axios('/v3/groups', {
      method: 'post',
      data: { name, enrollYear }
    })
  ).data as { id: string }
}

export async function renameGroupV3(gid: string, name: string) {
  await axios(`/v3/groups/${gid}/name`, {
    method: 'put',
    data: { name }
  })
  return true
}

export async function deleteGroupV3(gid: string) {
  await axios(`/v3/groups/${gid}`, {
    method: 'delete'
  })
  return true
}

export async function addGroupUserV3(gid: string, uid: string) {
  await axios(`/v3/groups/${gid}/users/${uid}`, {
    method: 'post'
  })
  return true
}

export async function removeGroupUserV3(gid: string, uid: string) {
  await axios(`/v3/groups/${gid}/users/${uid}`, {
    method: 'delete'
  })
  return true
}

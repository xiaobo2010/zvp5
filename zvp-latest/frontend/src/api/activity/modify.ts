import axios from '@/plugins/axios'

async function modifyActivityInfo(aid: string, name: string, description: string) {
    await axios({
      url: `/v2/activities/${aid}/info`,
      method: 'put',
      data: {
        description,
        name
      }
    })
}

async function modifyActivityStatus(aid: string, status: string) {
  await axios({
    url: `/v2/activities/${aid}/status`,
    method: 'put',
    data: {
      status
    }
  })
}

export { modifyActivityInfo as info, modifyActivityStatus as status }

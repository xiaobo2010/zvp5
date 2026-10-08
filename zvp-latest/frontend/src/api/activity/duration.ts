import axios from '@/plugins/axios'
import { ElMessage } from 'element-plus'
import type { ActivityMember } from '@/types/activity.v2'

export async function modify(
  user: string,
  aid: string,
  duration: number,
  mode: ActivityMember['mode'],
  msg: boolean = false
) {
  await axios({
    url: `/v2/activities/${aid}/members/${user.toString()}/record`,
    method: 'put',
    data: { duration, mode }
  })
  msg && ElMessage({
    message: 'Successfully modified duration',
    type: 'success',
    plain: true
  })
}

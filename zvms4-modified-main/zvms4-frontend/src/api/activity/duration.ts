import axios from '@/plugins/axios'
import { ElMessage } from 'element-plus'
import type { ActivityMember } from 'types/activity.v2'

export async function modify(
  user: string,
  aid: string,
  duration: number,
  mode: ActivityMember['mode'],
  options: { silent?: boolean } = {}
) {
  await axios({
    url: `/v2/activities/${aid}/members/${user.toString()}/record`,
    method: 'put',
    data: { duration, mode }
  })
  // 批量登记页逐行调用时自己聚合提示，避免 N 条 toast。
  if (options.silent) return
  ElMessage({
    message: 'Successfully modified duration',
    type: 'success',
    plain: true
  })
}

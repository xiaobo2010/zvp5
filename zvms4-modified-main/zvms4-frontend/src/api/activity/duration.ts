import axios from '@/plugins/axios'
import { ElMessage } from 'element-plus'
import type { ActivityMember } from 'types/activity.v2'

/**
 * 覆盖某个活动成员的时长。
 *
 * `options.timeout` 覆盖 axios 实例默认的 12s：批量登记页会并发调用，逐行覆盖
 * 时大活动的单次写事务可能超过默认超时。
 */
export async function modify(
  user: string,
  aid: string,
  duration: number,
  mode: ActivityMember['mode'],
  options: { silent?: boolean; timeout?: number } = {}
) {
  await axios({
    url: `/v2/activities/${aid}/members/${user.toString()}/record`,
    method: 'put',
    data: { duration, mode },
    timeout: options.timeout
  })
  // 批量登记页逐行调用时自己聚合提示，避免 N 条 toast。
  if (options.silent) return
  ElMessage({
    message: 'Successfully modified duration',
    type: 'success',
    plain: true
  })
}

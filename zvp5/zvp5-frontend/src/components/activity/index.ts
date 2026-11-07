import type { UserPosition, CreateActivityType } from '@/../types'

export { default as ZActivityCreate } from './ZActivityCreate.vue'
export { default as ZActivityDetails } from './ZActivityDetails.vue'
// ZActivityStatsDialog 不在桶里导出：它把 chart.js（~195KB）拖进 barrel chunk，
// 而 barrel 是组页等页面的静态依赖。入口只有 ZActivityDetails 的按需加载。
export { default as ZActivityList } from './ZActivityList.vue'
export { default as ZActivityMember } from './ZActivityMember.vue'
export { default as ZActivityMemberTimeJudge } from './ZTimeJudge.vue'
export { default as ZActivityCard } from './ZActivityCard.vue'
export { default as ZActivityMemberList } from './ZActivityMemberList.vue'
export { default as ZActivityPage } from './ZActivityPage.vue'
export { default as ZUserTimeJudge } from './ZUserTimeJudge.vue'
export { default as ZDetailedAnalysis } from './ZDetailedAnalysis.vue'
export { default as ZUploadFile } from './ZUploadFile.vue'
export function permissions(positions: UserPosition[]) {
  function upperStudent(positions: UserPosition[]) {
    const result =
      positions.includes('admin') ||
      positions.includes('department')
        ? true
        : false
    return result
  }
  function specialManagement(positions: UserPosition[]) {
    const result = positions.includes('admin') || positions.includes('department') ? true : false
    return result
  }
  const insert = {
    normal: upperStudent(positions),
    special: upperStudent(positions),
    merge: specialManagement(positions),
    upload: positions.includes('admin')
  } as Record<CreateActivityType, boolean>
  return insert
}

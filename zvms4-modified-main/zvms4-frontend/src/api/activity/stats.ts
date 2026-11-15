import axios from '@/plugins/axios'
import type { ActivityStatistics } from '@/../types'

async function getActivityAnalysis(aid: string): Promise<ActivityStatistics> {
  const response = await axios({
    url: `/v1/statistics/activities/${aid}/analysis`,
    method: 'get'
  })

  return response.data as ActivityStatistics
}

export { getActivityAnalysis as analysis }

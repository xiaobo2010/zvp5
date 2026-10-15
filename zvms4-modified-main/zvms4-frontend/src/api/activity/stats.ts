import axios from '@/plugins/axios'
import type {
  ActivityStatisticsDescription,
  ActivityStatisticsLayer
} from '@/../types'

async function getActivityStats(aid: string) {
  const response = await axios({
    url: `/v2/statistics/activities/${aid}/description`,
    method: 'get'
  })

  return response.data as ActivityStatisticsDescription
}

async function getActivityDistributionLayers(aid: string) {
  const response = await axios({
    url: `/v2/statistics/activities/${aid}/layers`,
    method: 'get'
  })

  return response.data as ActivityStatisticsLayer[]
}

export { getActivityStats as description, getActivityDistributionLayers as layers }

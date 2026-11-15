import type { Response, ActivityTimeRecord } from '@/../types'
import { ElNotification } from 'element-plus'
import axios from '@/plugins/axios.ts'

export * as activity from './activity'
export * as user from './user'
export * as group from './group'
export * as exports from './exports'
export * as logs from './logs'
export * as changelog from './changelog'
export * as notifications from './notifications'
export * as version from './version'
export * as attachment from './attachment'
export * as ai from './ai'
export const time = {
  async reads(
    search: string = '',
    page: number = 1,
    perpage: number = 5,
    sort: string = 'id',
    asc: boolean = true
  ) {
    const result = (
      await axios(`/v1/times`, {
        params: {
          search,
          page,
          perpage,
          sort,
          asc
        }
      })
    ).data as Response<ActivityTimeRecord[]> & {
      metadata: {
        size: number
      }
    }
    if (result.status === 'error') {
      ElNotification({
        title: 'Error when fetching data (' + result.code + ')',
        message: result.message,
        type: 'error'
      })
      return
    }
    return {
      time: result.data,
      size: result.metadata.size
    }
  }
}

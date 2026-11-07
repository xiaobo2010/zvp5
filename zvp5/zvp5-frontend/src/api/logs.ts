import type { Response } from '../../types'
import axios from '@/plugins/axios.ts'
import { ElNotification } from 'element-plus'
import { translateKey } from '@/utils/errorMessage'
import type { Log } from '../../types/log'

export async function read(
  page: number = 1,
  perpage: number = 10,
  query: string = '',
  user: string = '',
  xuehai_id: string = '',
  options: { types?: string[]; levels?: string[] } = {}
) {
  const result = (
    await axios('/logs', {
      params: {
        page,
        perpage,
        query,
        performer: user || undefined,
        xuehai_id: xuehai_id || undefined,
        type: options.types?.length ? options.types.join(',') : undefined,
        level: options.levels?.length ? options.levels.join(',') : undefined
      }
    })
  ).data as Response<Log[]> & {
    metadata: {
      size: number
    }
  }
  if (result.status === 'error') {
    ElNotification({
      title: translateKey('common.logListFetchFailed', { code: result.code }),
      message: result.message,
      type: 'error'
    })
    return
  }
  return {
    data: result.data,
    size: result.metadata.size
  }
}

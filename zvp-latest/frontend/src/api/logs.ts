import type { Response } from '@/types'
import axios from '@/plugins/axios.ts'
import type { Log } from '@/types/log'

export async function read(
  page: number = 1,
  perpage: number = 10,
  query: string = '',
  user: string = ''
) {
  const url = user ? `/users/${user}/logs` : '/logs'
  const result = (
    await axios(url, {
      params: {
        mode: 'campus',
        page,
        perpage,
        query
      }
    })
  ).data as Response<Log[]> & {
    metadata: {
      size: number
    }
  }
  return {
    data: result.data,
    size: result.metadata.size
  }
}

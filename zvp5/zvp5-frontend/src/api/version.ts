import axios from '@/plugins/axios.ts'
import type { Response } from '@/../types'

export async function getVersion(): Promise<string | undefined> {
  try {
    const response = await axios('/version')
    const data = response.data as Response<string>
    if (data.status === 'ok') {
      return data.data
    }
  } catch {
    return
  }
}

import axios from '@/plugins/axios.ts'
import type { Response } from '@/../types'

export interface ChangelogEntry {
  id: string
  version: string
  date: string
  items: string[]
}

export async function getChangelog(): Promise<ChangelogEntry[] | undefined> {
  try {
    const response = await axios('/changelog')
    const data = response.data as Response<ChangelogEntry[]>
    if (data.status === 'ok') {
      return data.data
    }
  } catch {
    return
  }
}

export async function createChangelog(
  payload: { version: string; date: string; items: string[] }
): Promise<void> {
  await axios({
    method: 'post',
    url: '/changelog',
    data: payload
  })
}

export async function updateChangelog(
  id: string,
  payload: { version: string; date: string; items: string[] }
): Promise<void> {
  await axios({
    method: 'put',
    url: `/changelog/${id}`,
    data: payload
  })
}

export async function deleteChangelog(id: string): Promise<void> {
  await axios({
    method: 'delete',
    url: `/changelog/${id}`
  })
}

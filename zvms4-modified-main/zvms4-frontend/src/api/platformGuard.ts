import axios from 'axios'
import type { Response } from '@/../types'
import { baseURL } from '@/plugins/axios'
import { getXuehaiId } from '@/plugins/ua'

export type PlatformSignalName = 'iframe' | 'bridge' | 'devtools'

export interface PlatformSignal {
  signal: PlatformSignalName
  detail?: string
}

export interface PlatformViolationResult {
  recorded: number
  duplicates: number
  banned: boolean
  rule_code?: string | null
}

/**
 * 上报客户端检测到的平台违规信号。
 *
 * 这里刻意不用 `@/plugins/axios` 实例:该实例会拉起 nprogress、并在失败时弹
 * ElMessage,而后台巡检是「尽力而为」的旁路,既不该让进度条在用户屏幕上闪,
 * 也不该因为后端未部署/网络抖动打断正常操作。因此直接走裸 axios + 手动补
 * Authorization / Xuehai-Id,失败静默返回 false,由调用方决定是否记节流。
 *
 * 返回 true 表示请求成功送达(不代表后端一定入库);false 表示未登录或请求失败。
 */
export async function reportSignals(signals: PlatformSignal[]): Promise<boolean> {
  if (signals.length === 0) {
    return true
  }
  const token = localStorage.getItem('token')
  if (!token) {
    return false
  }
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  const xuehaiId = getXuehaiId()
  if (xuehaiId > 0) {
    headers['Xuehai-Id'] = String(xuehaiId)
  }
  try {
    const response = await axios.post<Response<PlatformViolationResult>>(
      `${baseURL}/v1/ban/violations`,
      { signals },
      { headers, withCredentials: true, timeout: 8000 }
    )
    return response.data.status !== 'error'
  } catch {
    return false
  }
}

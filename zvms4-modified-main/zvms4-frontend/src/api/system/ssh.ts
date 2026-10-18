import axios from '@/plugins/axios.ts'
import { baseURL } from '@/plugins/axios.ts'

// POST /api/system/ssh/session 的响应体(后端 routers/system_ssh_router.py:74)
export interface SshSessionData {
  ticket: string
  expires_in: number
}

export interface SshSessionResponse {
  status: string
  code: number
  data: SshSessionData
}

// 双人码换取一次性 ticket;ticket 单次使用、120s 过期、绑定操作者
export function createSshSession(totpCode: string) {
  return axios.post<SshSessionResponse>('/system/ssh/session', {
    totp_code: totpCode
  })
}

// WS 实际路由为 /api/system/ssh/ws(后端 @router.websocket('/ws') +
// main.py 挂载前缀 /api/system/ssh);ticket 走查询参数。
// 浏览器握手带不了 Authorization 头,所以只认 ticket。
export function buildSshWsUrl(ticket: string): string {
  const apiBase = baseURL || '/api'
  const path = `${apiBase.replace(/\/+$/, '')}/system/ssh/ws`
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}${path}?ticket=${encodeURIComponent(ticket)}`
}

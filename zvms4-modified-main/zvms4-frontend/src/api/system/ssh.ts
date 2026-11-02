import axios from '@/plugins/axios.ts'
import { baseURL } from '@/plugins/axios.ts'
import type { AiThinkingLevel } from '@/../types/ai'

// GET /api/system/ssh/targets 的响应体:目标目录由服务端下发,前端只拿展示字段。
export interface SshTarget {
  id: string
  label: string
  display: string
}

export interface SshTargetsData {
  targets: SshTarget[]
  default: string
}

export interface SshTargetsResponse {
  status: string
  code: number
  data: SshTargetsData
}

// 连接时选中的目标;后端回显 id + label,供会话审计与展示。
export interface SshSessionTarget {
  id: string
  label: string
}

// POST /api/system/ssh/session 的响应体(后端 routers/system_ssh_router.py:74)
export interface SshSessionData {
  ticket: string
  expires_in: number
  target?: SshSessionTarget
}

export interface SshSessionResponse {
  status: string
  code: number
  data: SshSessionData
}

// 目标目录只读:前端不做任何地址推导,全部以服务端配置为准。
export function listSshTargets() {
  return axios.get<SshTargetsResponse>('/system/ssh/targets')
}

// 双人码换取一次性 ticket;ticket 单次使用、120s 过期、绑定操作者。
// target 省略时后端用默认目标;未选目标则不下发该字段。
export function createSshSession(totpCode: string, target?: string) {
  return axios.post<SshSessionResponse>('/system/ssh/session', {
    totp_code: totpCode,
    ...(target ? { target } : {})
  })
}

// POST /api/system/ssh/suggest 的响应体:后端只生成命令文本,不执行任何东西。
export interface SshSuggestData {
  command: string
  explanation: string
  model: string
  request_id: string
}

export interface SshSuggestResponse {
  status: string
  code: number
  data: SshSuggestData
}

// 让 AI 依据自然语言意图生成一条 shell 命令(仅文本,不执行)。
// 错误 detail 为 AI_* 码(AI_NO_PROVIDER / model_not_found / AI_BUSY …)。
// thinking 为空时不下发,后端取默认档。
export function suggestSshCommand(
  intent: string,
  context?: string,
  model?: string,
  thinking?: AiThinkingLevel | null
) {
  return axios.post<SshSuggestResponse>('/system/ssh/suggest', {
    intent,
    context: context || undefined,
    model: model || undefined,
    thinking: thinking || undefined
  })
}

// WS 实际路由为 /api/system/ssh/ws(后端 @router.websocket('/ws') +
// main.py 挂载前缀 /api/system/ssh);ticket 走查询参数。
// 浏览器握手带不了 Authorization 头,所以只认 ticket。
export function buildSshWsUrlFor(
  apiBase: string,
  ticket: string,
  pageOrigin = window.location.origin
): string {
  const apiUrl = new URL(apiBase || '/api', pageOrigin)
  const path = `${apiUrl.pathname.replace(/\/+$/, '')}/system/ssh/ws`
  const url = new URL(path, apiUrl.origin)
  url.protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('ticket', ticket)
  return url.toString()
}

// SSH 由 API 服务器的网关后端发起,WS 必须打 API host;
// 页面/content host 只负责文件管理。
export function buildSshWsUrl(ticket: string): string {
  return buildSshWsUrlFor(baseURL || '/api', ticket)
}

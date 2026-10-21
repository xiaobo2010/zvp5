import type { Response } from '@/../types'
import axios from '@/plugins/axios.ts'

export interface BanRuleConfig {
  threshold: number
  window_hours: number
}

export interface BanRule {
  code: string
  name: string
  enabled: boolean
  builtin: boolean
  config: Partial<BanRuleConfig>
  penalties: number[]
  updated_at: string
}

export interface BanRuleUpdate {
  enabled?: boolean
  config?: Partial<BanRuleConfig>
  penalties?: number[]
}

export interface BanRecord {
  _id: string
  user_oid: string
  user_name: string
  user_id: string
  source: 'admin' | 'rule'
  rule_code?: string
  reason: string
  permanent: boolean
  started_at: string
  expires_at?: string | null
  operator_name?: string
}

export async function getRules() {
  const result = (await axios('/v3/ban/rules')).data as Response<BanRule[]>
  if (result.status === 'error') {
    throw result
  }
  return result.data
}

export async function updateRule(code: string, payload: BanRuleUpdate) {
  const result = (
    await axios({
      method: 'put',
      url: `/v3/ban/rules/${code}`,
      data: payload
    })
  ).data as Response<BanRule>
  if (result.status === 'error') {
    throw result
  }
  return result.data
}

export async function getRecords(page: number = 1, perpage: number = 20, query: string = '') {
  const result = (
    await axios('/v3/ban/records', {
      params: {
        page,
        perpage,
        query
      }
    })
  ).data as Response<BanRecord[]> & {
    metadata: {
      size: number
      page: number
      perpage: number
    }
  }
  if (result.status === 'error') {
    throw result
  }
  return {
    records: result.data,
    size: result.metadata.size
  }
}

export interface BannedUser {
  oid: string
  name: string
  user_id: string
  class_name: string
  reason?: string | null
  permanent: boolean
  until?: string | null
  source?: 'admin' | 'rule' | null
  rule_code?: string | null
}

export async function getBannedUsers(page: number = 1, perpage: number = 20, query: string = '') {
  const result = (
    await axios('/v3/ban/users', {
      params: {
        page,
        perpage,
        query
      }
    })
  ).data as Response<BannedUser[]> & {
    metadata: {
      size: number
      page: number
      perpage: number
    }
  }
  if (result.status === 'error') {
    throw result
  }
  return {
    users: result.data,
    size: result.metadata.size
  }
}

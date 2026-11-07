import type { Response } from '@/../types'
import axios from '@/plugins/axios.ts'

export interface BanRuleConfig {
  threshold: number
  window_hours: number
}

export interface BanRule {
  code: string
  name: string
  trigger: string
  trigger_name: string
  enabled: boolean
  builtin: boolean
  config: Partial<BanRuleConfig>
  // 正数为封禁天数,null 表示永久(只能是梯度末位)
  penalties: (number | null)[]
  updated_at: string
}

export interface BanRuleUpdate {
  enabled?: boolean
  name?: string
  config?: Partial<BanRuleConfig>
  penalties?: (number | null)[]
}

export interface BanRuleCreate {
  code: string
  name: string
  trigger: string
  config: BanRuleConfig
  penalties: (number | null)[]
  enabled: boolean
}

export interface BanTrigger {
  trigger: string
  name: string
  evaluation: 'login' | 'report'
  description: string
  config: Partial<BanRuleConfig>
}

export interface BanViolation {
  _id: string
  user_oid: string
  user_name: string
  user_id: string
  trigger: string
  signal: string
  detail: string | null
  xuehai_id: string | null
  created_at: string
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

export async function getTriggers() {
  const result = (await axios('/v3/ban/triggers')).data as Response<BanTrigger[]>
  if (result.status === 'error') {
    throw result
  }
  return result.data
}

export async function createRule(payload: BanRuleCreate, verificationToken?: string) {
  const result = (
    await axios({
      method: 'post',
      url: '/v3/ban/rules',
      data: { ...payload, ...(verificationToken ? { verification_token: verificationToken } : {}) }
    })
  ).data as Response<BanRule>
  if (result.status === 'error') {
    throw result
  }
  return result.data
}

export async function deleteRule(code: string, verificationToken?: string) {
  const result = (
    await axios({
      method: 'delete',
      url: `/v3/ban/rules/${code}`,
      ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
    })
  ).data as Response<null>
  if (result.status === 'error') {
    throw result
  }
  return true
}

export async function getViolations(page: number = 1, perpage: number = 20, query: string = '') {
  const result = (
    await axios('/v3/ban/violations', {
      params: {
        page,
        perpage,
        query
      }
    })
  ).data as Response<BanViolation[]> & {
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
    violations: result.data,
    size: result.metadata.size
  }
}

export async function updateRule(
  code: string,
  payload: BanRuleUpdate,
  verificationToken?: string
) {
  const result = (
    await axios({
      method: 'put',
      url: `/v3/ban/rules/${code}`,
      data: { ...payload, ...(verificationToken ? { verification_token: verificationToken } : {}) }
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

export interface BannedDeviceOwner {
  id: string
  name: string
}

export interface BannedDevice {
  xuehai_id: string
  reason: string | null
  until: string | null
  source: 'rule' | 'admin'
  rule_code: string | null
  created_at: string
  ban_count: number
  owner: BannedDeviceOwner | null
}

export interface DeviceBanPayload {
  xuehai_id: string
  reason: string
  until: string | null
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

export async function getBannedDevices(
  page: number = 1,
  perpage: number = 20,
  query: string = ''
) {
  const result = (
    await axios('/v3/ban/devices', {
      params: {
        page,
        perpage,
        query
      }
    })
  ).data as Response<BannedDevice[] | { devices: BannedDevice[]; total: number }> & {
    metadata?: {
      size?: number
    }
  }
  if (result.status === 'error') {
    throw result
  }
  const payload = result.data
  if (Array.isArray(payload)) {
    return {
      devices: payload,
      total: result.metadata?.size ?? payload.length
    }
  }
  return {
    devices: payload?.devices ?? [],
    total: payload?.total ?? result.metadata?.size ?? 0
  }
}

export async function banDevice(payload: DeviceBanPayload, verificationToken?: string) {
  await axios({
    method: 'post',
    url: '/v3/ban/devices',
    data: { ...payload, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
  return true
}

export async function unbanDevice(xuehaiId: string, verificationToken?: string) {
  await axios({
    method: 'delete',
    url: `/v3/ban/devices/${encodeURIComponent(xuehaiId)}`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
  return true
}

export async function resetDeviceGradient(xuehaiId: string, verificationToken?: string) {
  await axios({
    method: 'post',
    url: `/v3/ban/devices/${encodeURIComponent(xuehaiId)}/reset-gradient`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
  return true
}

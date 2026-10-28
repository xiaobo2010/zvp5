import { aiRequest } from '@/api/ai/http'

/**
 * 角色级策略（/system/role-policies）。
 *
 * 一个角色一行策略：`features` 是细粒度功能开关，`quotas` 是月度 AI 配额。
 * 写操作（PUT）要另一位管理员的 6 位动态码，按仓库既有约定放进请求体
 * `totp_code`（同 src/api/system/dbContent.ts、src/api/system/files.ts）。
 *
 * 请求层复用 AI 网关那套（src/api/ai/http.ts 的 aiRequest）：它已经负责
 * 「只判状态码 + 拆 envelope + 401 跳登录 + 维护模式跳转 + 把后端 detail
 * 原文带进异常」，这里不再另造一套 envelope 拆法。
 *
 * 配额单位：`chat` 是额度单位（后端每 1000 token 记 1 单位），`ocr` 是每月
 * 次数，`schedule` 是 AI 定时任务条数。三者都用 null 表示「用系统默认」，
 * 0 表示「该角色不开放」——0 与 null 语义不同，不要互相折叠。
 */

/**
 * 13 个功能开关的 key，顺序即页面展示顺序。
 *
 * 后端 GET 会下发 `feature_keys`，以前端渲染为准；这里的常量只作兜底
 * （首次加载失败后在本地仍然能画出完整矩阵）。
 */
export const ROLE_FEATURE_KEYS = [
  'ai.chat',
  'ai.ocr',
  'ai.agent',
  'ai.schedule',
  'ai.keys',
  'activity.stats',
  'attachment.manage',
  'export.time',
  'import.activity',
  'logs.read',
  'notify.publish',
  'ban.read',
  'quota.manage'
] as const

/** 单个功能 key。 */
export type RoleFeatureKey = (typeof ROLE_FEATURE_KEYS)[number]

/** 三类月度配额。 */
export type RoleQuotaKind = 'chat' | 'ocr' | 'schedule'

/** 月度配额；null = 用系统默认，0 = 不开放。 */
export type RoleQuotas = Record<RoleQuotaKind, number | null>

/** 功能开关表：key → 是否可用。后端可能只下发部分 key，读的时候按缺失当 true。 */
export type RoleFeatures = Record<string, boolean>

/** 一个角色的策略。 */
export interface RolePolicy {
  role: string
  features: RoleFeatures
  quotas: RoleQuotas
  /** 最后修改时间；从未改过为 null。 */
  updated_at: string | null
  /** 最后修改人 oid；从未改过为 null。 */
  updated_by: string | null
}

/** GET /system/role-policies 的 data。 */
export interface RolePoliciesData {
  roles: RolePolicy[]
  /** 可写角色；admin 不在此列（固定全量权限）。 */
  editable_roles: string[]
  /** 后端认可的功能 key 全集，顺序即展示顺序。 */
  feature_keys: string[]
}

/** PUT 的请求体（不含动态码）。 */
export interface RolePolicyInput {
  features: RoleFeatures
  quotas: RoleQuotas
}

/** 读全部角色策略。 */
export async function getRolePolicies(): Promise<RolePoliciesData> {
  return aiRequest<RolePoliciesData>({
    method: 'get',
    url: '/system/role-policies'
  })
}

/**
 * 写单个角色的策略；需要另一位管理员的 6 位动态码。
 * 响应是更新后的该角色策略，调用方直接拿来回填。
 */
export async function updateRolePolicy(
  role: string,
  input: RolePolicyInput,
  totpCode: string
): Promise<RolePolicy> {
  return aiRequest<RolePolicy>({
    method: 'put',
    url: `/system/role-policies/${encodeURIComponent(role)}`,
    data: {
      features: input.features,
      quotas: input.quotas,
      totp_code: totpCode
    }
  })
}

/** 本人生效策略：形状同单角色策略（roles 里取最高优先级的那个）。 */
export type MyRolePolicy = RolePolicy

/** 读当前登录用户实际生效的策略（多角色合并后的结果）。 */
export async function getMyRolePolicy(): Promise<MyRolePolicy> {
  return aiRequest<MyRolePolicy>({
    method: 'get',
    url: '/system/role-policies/me'
  })
}

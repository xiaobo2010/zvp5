import { aiRequest } from '@/api/ai/http'

/**
 * 用户配额（/system/user-quotas）。
 *
 * 只有 admin 可读写；非 admin 一律 403。
 *
 * 请求层复用 AI 网关那套 envelope 拆法（src/api/ai/http.ts 的 aiRequest），
 * 与 src/api/system/settings.ts 同一约定：它已经负责「只判状态码 + 拆 envelope
 * + 401 跳登录 + 维护模式跳转 + 把后端 detail 带进异常」。
 *
 * 字段单位：`daily_max_bytes` 是**字节**，界面按 MB 展示时由调用方换算。
 */

/** 覆盖值；null 表示不覆盖、继承全局。 */
export interface UserQuotaOverrides {
  daily_max_files: number | null
  daily_max_bytes: number | null
}

/** 实际生效值：全局设置与用户覆盖合并后的结果，永远有具体数字。 */
export interface UserQuotaEffective {
  daily_max_files: number
  daily_max_bytes: number
}

export interface UserQuotaView extends UserQuotaOverrides {
  user_oid: string
  effective: UserQuotaEffective
}

/** 读某个用户的配额（含覆盖值与实际生效值）。 */
export async function getUserQuota(userOid: string): Promise<UserQuotaView> {
  return aiRequest<UserQuotaView>({
    method: 'get',
    url: `/system/user-quotas/${encodeURIComponent(userOid)}`
  })
}

/** 写某个用户的配额；null 即清除覆盖、继承全局。响应同 GET。 */
export async function setUserQuota(
  userOid: string,
  input: UserQuotaOverrides
): Promise<UserQuotaView> {
  return aiRequest<UserQuotaView>({
    method: 'put',
    url: `/system/user-quotas/${encodeURIComponent(userOid)}`,
    data: {
      daily_max_files: input.daily_max_files,
      daily_max_bytes: input.daily_max_bytes
    }
  })
}

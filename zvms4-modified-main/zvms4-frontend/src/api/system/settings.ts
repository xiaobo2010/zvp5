import { aiRequest } from '@/api/ai/http'
import { resolveErrorMessageOr } from '@/utils/errorMessage'

/**
 * 在线设置中心（/system/settings）。
 *
 * 严格 admin：非 admin 一律 403。
 *
 * 请求层直接复用 AI 网关那套（src/api/ai/http.ts 的 aiRequest）：它已经负责
 * 「只判状态码 + 拆 envelope + 401 跳登录 + 维护模式跳转 + 把后端的 detail
 * 原文带进异常」，这里不再另造一套 envelope 拆法（约定见 src/api/notifications.ts
 * 的同类 unwrap）。
 */

/** 设置项的值类型。后端按这个字段决定前端用哪种控件渲染。 */
export type SettingValueType = 'int' | 'bool' | 'str' | 'list' | 'dict'

/** 设置分组：ai = AI 网关，ops = 运维阈值。 */
export type SettingGroup = 'ai' | 'ops'

/** 设置值的标量部分。 */
export type SettingScalar = string | number | boolean | null

/** 角色额度表：{ 角色: { 能力: 数量 } }。 */
export type SettingRoleQuota = { [role: string]: { [kind: string]: number } }

/**
 * 设置值：已是解析后的 JSON 值，不是字符串。
 *
 * 刻意写成非递归的类型：递归别名会让 Vue 的 UnwrapRef 无限展开，
 * 任何 ref/computed 包住它都会触发「类型实例化过深」。
 */
export type SettingValue = SettingScalar | SettingScalar[] | SettingRoleQuota

export interface SystemSettingItem {
  /** 稳定标识，例如 ai.rate_limit_per_minute。 */
  key: string
  /** 当前生效值（已解析）。 */
  value: SettingValue
  type: SettingValueType
  group: SettingGroup
  /** 展示用标题，后端下发，前端不自行拼。 */
  label: string
  /** 补充说明，可为空。 */
  description: string | null
  /** int 类型的取值下界 / 上界，可为空。 */
  min: number | null
  max: number | null
  /** list 类型的候选值（角色枚举等），没有候选值时为空。 */
  options: string[] | null
  /** 最后修改时间，可为空（从未被改过）。 */
  updated_at: string | null
  /** 最后修改人的 oid，可为空。 */
  updated_by: string | null
}

export interface SystemSettingsData {
  items: SystemSettingItem[]
  /** 内置默认值，用来提示「这一项被改过」。 */
  defaults: Record<string, SettingValue>
}

/** PUT 的单项：只带 key 与新值。 */
export interface SystemSettingUpdate {
  key: string
  value: SettingValue
}

/** 读取全部可在线修改的设置项。 */
export async function listSettings(): Promise<SystemSettingsData> {
  return aiRequest<SystemSettingsData>({ method: 'get', url: '/system/settings' })
}

/**
 * 保存设置。只提交发生变化的键；返回与 GET 同构，但 items 只含本次改动的键。
 * 校验失败时后端返回 400，detail 是可直接展示给人看的说明。
 */
export async function updateSettings(
  items: SystemSettingUpdate[],
  verificationToken?: string
): Promise<SystemSettingsData> {
  return aiRequest<SystemSettingsData>({
    method: 'put',
    url: '/system/settings',
    data: { items, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
}

/** 内部代号（AI_HTTP_400 之类）不是人话，展示时要换成兜底文案。 */
function isInternalCode(message: string): boolean {
  return /^AI_[A-Z0-9_]+$/.test(message)
}

/**
 * 从异常里取出能直接展示给管理员的话。
 * 后端 400 的 detail 是人话，直接原样透出；取不到才回落兜底文案。
 */
export function settingErrorMessage(error: unknown, fallback: string): string {
  const message = resolveErrorMessageOr(error, '').trim()
  if (message && !isInternalCode(message)) {
    return message
  }
  return fallback
}

/** 取出 HTTP 状态码；非 axios 语义的异常返回 null。 */
export function settingErrorStatus(error: unknown): number | null {
  if (error && typeof error === 'object' && 'status' in error) {
    const status = (error as { status?: unknown }).status
    if (typeof status === 'number') {
      return status
    }
  }
  return null
}

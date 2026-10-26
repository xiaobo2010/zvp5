import type { AiQuotaEntry, AiQuotaKind, AiQuotaMutation } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * 额度（/ai/quota）。
 *
 * 没有「列出所有用户额度」的端点：管理端形态是「先选人，再查看与调整」。
 */

/**
 * 上限的来源。
 *
 * 语义坑：`explicit` **不等于**「管理员手调过」——首次扣费会把当时的默认上限
 * 固化进 limit_count，所以 UI 只能写「库内已固化」，不能写「管理员已设置」。
 */
export type AiQuotaSource = 'explicit' | 'role_default' | 'global_default'

/** 带来源的额度项：types/ai.d.ts 的 AiQuotaEntry 没有 source 字段。 */
export interface AiQuotaEntryView extends AiQuotaEntry {
  /** 老版本后端没有该字段时为 undefined，UI 自行降级为不显示标签。 */
  source?: AiQuotaSource
}

export interface AiQuotaSnapshotView {
  ocr: AiQuotaEntryView
  chat: AiQuotaEntryView
}

/**
 * 来源 → i18n key。
 *
 * 注意「库内已固化」的措辞：explicit 只说明这个数字已经写进 limit_count，
 * 不代表管理员手调过（首次扣费同样会固化默认值）。
 */
export const AI_QUOTA_SOURCE_LABEL_KEYS: Record<AiQuotaSource, string> = {
  explicit: 'ai.quotaAdmin.sourceExplicit',
  role_default: 'ai.quotaAdmin.sourceRoleDefault',
  global_default: 'ai.quotaAdmin.sourceGlobalDefault'
}

/** 来源的展示文案 key；未知/缺失返回 null，调用方不渲染标签。 */
export function aiQuotaSourceLabelKey(source?: AiQuotaSource | null): string | null {
  return source ? (AI_QUOTA_SOURCE_LABEL_KEYS[source] ?? null) : null
}

/** 当前用户本月额度自查。 */
export async function myQuota(): Promise<AiQuotaSnapshotView> {
  return aiRequest<AiQuotaSnapshotView>({ method: 'get', url: '/ai/quota' })
}

/** admin 查看某个用户的额度。 */
export async function userQuota(userOid: string): Promise<AiQuotaSnapshotView> {
  return aiRequest<AiQuotaSnapshotView>({ method: 'get', url: `/ai/quota/${userOid}` })
}

/** 设置某能力的月上限；请求字段是 limit_count，响应字段是 limit。 */
export async function setUserLimit(
  userOid: string,
  kind: AiQuotaKind,
  limitCount: number
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'put',
    url: `/ai/quota/${userOid}`,
    data: { kind, limit_count: limitCount }
  })
}

/** 重置某能力本月已用量（上限保留）。 */
export async function resetUserQuota(
  userOid: string,
  kind: AiQuotaKind
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'post',
    url: `/ai/quota/${userOid}/reset`,
    data: { kind }
  })
}

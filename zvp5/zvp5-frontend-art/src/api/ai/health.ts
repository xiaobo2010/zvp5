import type { AiHealthData } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * 网关健康视图（/ai/health）。仅 admin 可用。
 *
 * 视图本身不含任何密钥材料：站点与个人 Key 只给数量与脱敏后的主机名。
 */
export async function getHealth(): Promise<AiHealthData> {
  return aiRequest<AiHealthData>({ method: 'get', url: '/ai/health' })
}

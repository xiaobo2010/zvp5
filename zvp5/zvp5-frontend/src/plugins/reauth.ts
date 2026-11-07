import { isTemporaryTokenCancelled, withTemporaryToken } from '@/plugins/short-token'
import { useUserStore } from '@/stores/user'

export function isReauthCancelled(error: unknown): boolean {
  return isTemporaryTokenCancelled(error)
}

export function withReauth<T>(reason: string, action: (token: string) => Promise<T>): Promise<T> {
  const userid = String(useUserStore()._id ?? '')
  return withTemporaryToken(userid, reason, action)
}

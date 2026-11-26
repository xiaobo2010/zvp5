import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import api from '@/api'
import i18n from '@/i18n'

const VERIFY_TTL_MS = 5 * 60 * 1000
const VERIFY_SKEW_MS = 5 * 1000

interface VerifyCache {
  token: string
  expiresAt: number
}

const cache = new Map<string, VerifyCache>()
let pendingKey = ''
let pendingToken: Promise<string> | null = null

export function cachedTemporaryToken(userid: string): string {
  const entry = cache.get(userid)
  if (!entry) {
    return ''
  }
  if (Date.now() >= entry.expiresAt - VERIFY_SKEW_MS) {
    cache.delete(userid)
    return ''
  }
  return entry.token
}

export function clearTemporaryToken(userid?: string): void {
  if (userid === undefined) {
    cache.clear()
    return
  }
  cache.delete(userid)
}

/** 密码框被用户关掉（取消 / 右上角 close）时返回 true，调用方应静默中止。 */
export function isTemporaryTokenCancelled(error: unknown): boolean {
  if (typeof error === 'string') {
    return error === 'cancel' || error === 'close'
  }
  if (!error || typeof error !== 'object') {
    return false
  }
  const action = (error as { action?: unknown }).action
  return action === 'cancel' || action === 'close'
}

function promptToken(userid: string, reason?: string): Promise<string> {
  const locale = i18n.global.locale.value
  const locales: Record<string, { title: string; message: string; confirm: string; cancel: string }> = {
    'zh-CN': {
      title: '请输入密码',
      // 解锁场景由调用方给更具体的提示（如平板访问附件），默认保持原来的问法。
      message: reason || '请确认您的密码。',
      confirm: '确认',
      cancel: '取消'
    },
    'en-US': {
      title: '',
      message: '',
      confirm: '',
      cancel: ''
    }
  }
  const copy = locales[locale] ?? locales['zh-CN']
  return new Promise((resolve, reject) => {
    ElMessageBox.prompt(copy.message, copy.title, {
      confirmButtonText: copy.confirm,
      cancelButtonText: copy.cancel,
      inputType: 'password',
      type: 'warning',
      showClose: false,
      closeOnClickModal: false,
      closeOnPressEscape: false,
    })
      .then(({ value }) => {
        api.user.auth
          .useLongTermAuth(userid, value, 'short', 'verify')
          .then((result) => {
            if (result) {
              resolve(result.token)
            } else {
              reject('Login failed')
            }
          })
          .catch((err) => {
            reject(err)
          })
      })
      .catch((err) => {
        reject(err)
      })
  })
}

export async function temporaryToken(userid: string, reason?: string): Promise<string> {
  const cached = cachedTemporaryToken(userid)
  if (cached) {
    return cached
  }
  if (pendingToken && pendingKey === userid) {
    return pendingToken
  }
  pendingKey = userid
  pendingToken = promptToken(userid, reason)
    .then((token) => {
      cache.set(userid, { token, expiresAt: Date.now() + VERIFY_TTL_MS })
      return token
    })
    .finally(() => {
      pendingToken = null
      pendingKey = ''
    })
  return pendingToken
}

/** 401/403 表示这张凭证被后端否了（过期/换账号），清缓存后可重弹一次密码。 */
function isVerificationRejected(error: unknown): boolean {
  if (!error || typeof error !== 'object') {
    return false
  }
  const status =
    (error as { status?: unknown }).status ??
    (error as { response?: { status?: unknown } }).response?.status
  return status === 401 || status === 403
}

/**
 * 高危动作的统一入口：先取（或弹框换）一张 verify 短令牌，失败一次就清缓存重来一遍。
 *
 * 只重试一次：再失败说明不是凭证问题，继续循环只会把用户困在密码框里。
 */
export async function withTemporaryToken<T>(
  userid: string,
  reason: string | undefined,
  action: (token: string) => Promise<T>
): Promise<T> {
  const token = await temporaryToken(userid, reason)
  try {
    return await action(token)
  } catch (error) {
    if (!isVerificationRejected(error)) {
      throw error
    }
    clearTemporaryToken(userid)
    const retried = await temporaryToken(userid, reason)
    return action(retried)
  }
}

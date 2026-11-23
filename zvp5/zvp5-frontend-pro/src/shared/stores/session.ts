// Lightweight session store shared by the Pro console pages.
//
// Pro does not use Pinia; this module-level reactive singleton is enough for
// the handful of pages that need the signed-in account (notably the secretary
// roster, which must read `class_id` from the profile rather than `/v1/users`).
// API calls stay in `@/api/canary`; this file only caches their result.

import { reactive } from 'vue'
import { fetchProfile, type CanaryUser } from '@/api/canary'

export interface SessionState {
  profile: CanaryUser | null
  loading: boolean
  loaded: boolean
  error: string
}

let pending: Promise<CanaryUser | null> | null = null
let loadedToken = ''

function currentToken(): string {
  try {
    return localStorage.getItem('token') ?? ''
  } catch {
    return ''
  }
}

async function loadProfile(force = false): Promise<CanaryUser | null> {
  const token = currentToken()
  if (!force && pending) return pending
  if (!force && session.profile && loadedToken === token) return session.profile
  if (session.profile && loadedToken !== token) {
    // A different account signed in; never expose the previous profile while
    // the fresh request is in flight.
    session.profile = null
    session.loaded = false
  }

  session.loading = true
  session.error = ''

  const request = fetchProfile()
    .then((profile) => {
      // A login/logout while the request was in flight must not cache the
      // previous account's profile.
      if (currentToken() !== token) return null
      session.profile = profile
      loadedToken = token
      session.loaded = true
      return profile
    })
    .catch((reason: unknown) => {
      session.profile = null
      session.loaded = false
      loadedToken = ''
      session.error = reason instanceof Error ? reason.message : '账号信息加载失败。'
      return null
    })
    .finally(() => {
      session.loading = false
      if (pending === request) pending = null
    })

  pending = request
  return request
}

const session = reactive<SessionState & { ensureProfile: (force?: boolean) => Promise<CanaryUser | null> }>({
  profile: null,
  loading: false,
  loaded: false,
  error: '',
  ensureProfile: loadProfile
})

export function useSessionStore() {
  return session
}

/** Drop the cached profile after a login/logout so the next page refetches it. */
export function resetSessionStore() {
  session.profile = null
  session.loading = false
  session.loaded = false
  session.error = ''
  pending = null
  loadedToken = ''
}

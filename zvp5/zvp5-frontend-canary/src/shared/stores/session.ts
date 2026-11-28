import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { FeaturePolicy, LoginResult, UserProfile } from '@/types/domain'
import {
  fetchMyPolicy,
  fetchProfile,
  loginWithPassword,
  loginWithTempPassword,
  logout as apiLogout
} from '@/shared/api/auth'
import { readJson, readToken, tokenSubject, writeJson, writeToken } from '@/shared/lib/storage'

const PROFILE_KEY = 'zvp5.profile'

/**
 * 会话域：只负责身份、令牌、角色与功能策略。
 * 业务数据（时长、活动）不放在这里，避免旧版 store 过载的问题。
 */
export const useSessionStore = defineStore('session', () => {
  const token = ref(readToken())
  const cachedProfile = readJson<UserProfile>(PROFILE_KEY)
  const profile = ref<UserProfile | null>(
    cachedProfile?._id === tokenSubject(token.value) ? cachedProfile : null
  )
  const policy = ref<FeaturePolicy | null>(null)
  const busy = ref(false)
  const weakPassword = ref(false)
  const deviceChallenge = ref('')

  const authenticated = computed(() => Boolean(token.value))
  const roles = computed(() => profile.value?.roles ?? [])
  const isAdmin = computed(() => roles.value.includes('admin'))
  const isManager = computed(
    () => roles.value.includes('admin') || roles.value.includes('department')
  )
  const displayName = computed(() => profile.value?.name ?? '')

  function can(feature: string): boolean {
    if (isAdmin.value) return true
    return policy.value?.features?.[feature] === true
  }

  function persistProfile(next: UserProfile | null) {
    profile.value = next
    if (next) writeJson(PROFILE_KEY, next)
  }

  async function loadPolicy(): Promise<void> {
    try {
      policy.value = await fetchMyPolicy()
    } catch {
      // 策略读取失败不阻断登录：角色仍可决定主导航，feature 默认关闭。
      policy.value = null
    }
  }

  async function applyLogin(result: LoginResult): Promise<void> {
    token.value = result.token
    writeToken(result.token)
    weakPassword.value = Boolean(result.weak_password)
    const next = result.profile ?? (await fetchProfile(result._id))
    persistProfile(next)
    await loadPolicy()
  }

  async function signIn(id: string, password: string): Promise<void> {
    busy.value = true
    try {
      const result = await loginWithPassword(id, password)
      await applyLogin(result)
    } finally {
      busy.value = false
    }
  }

  async function signInWithTemp(challenge: string, tempPassword: string): Promise<void> {
    busy.value = true
    try {
      const result = await loginWithTempPassword(challenge, tempPassword)
      await applyLogin(result)
    } finally {
      busy.value = false
    }
  }

  async function bootstrap(): Promise<void> {
    if (!token.value) return
    const current = token.value
    if (!profile.value) {
      const oid = tokenSubject(current)
      if (!oid) return
      try {
        const next = await fetchProfile(oid)
        if (token.value !== current) return
        persistProfile(next)
      } catch {
        // 401 由 HTTP 层处理，临时网络故障保留会话等待重试。
        return
      }
    }
    await loadPolicy()
  }

  async function signOut(): Promise<void> {
    await apiLogout()
    reset()
  }

  function reset(): void {
    token.value = ''
    weakPassword.value = false
    deviceChallenge.value = ''
    policy.value = null
    writeToken('')
    persistProfile(null)
    try {
      localStorage.removeItem(PROFILE_KEY)
    } catch {
      // ignore
    }
  }

  return {
    token,
    profile,
    policy,
    busy,
    weakPassword,
    deviceChallenge,
    authenticated,
    roles,
    isAdmin,
    isManager,
    displayName,
    can,
    signIn,
    signInWithTemp,
    bootstrap,
    signOut,
    reset,
    loadPolicy
  }
})

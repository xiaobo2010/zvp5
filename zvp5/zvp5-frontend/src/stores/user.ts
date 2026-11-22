import api from '@/api'
import type { User, UserPosition, UserProfile, UserActivityTimeSums } from '@/../types'
import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus/es/components/notification/index.mjs'
import { useRouter } from 'vue-router'
import { getUserPositions } from '@/utils/groupPosition'
import { clearCache } from '@/utils/idbCache'
import { clearMemoryCache } from '@/utils/requestCache'
import { useGroupsStore } from '@/stores/groups'
import { readStoredTheme, setStoredTheme, themePath, isRootApp } from '@/utils/theme'

interface UserTime {
  onCampus: number
  offCampus: number
  socialPractice: number
}

/**
 * @description Normalize the backend kebab-case duration payload into the
 * camelCase shape kept inside the store.
 */
function normalizeUserTime(sums: UserActivityTimeSums): UserTime {
  return {
    onCampus: sums['on-campus'],
    offCampus: sums['off-campus'],
    socialPractice: sums['social-practice']
  }
}

export const useUserStore = defineStore('user', {
  state: () => ({
    id: 0 as number | string,
    _id: '',
    name: '',
    position: [] as UserPosition[],
    groups: [] as string[],
    class_id: '',
    isLogin: false,
    shouldResetPassword: false,
    banned: false,
    bannedNoticeAt: 0,
    // 封禁原因与到期时间都来自 403 响应头(X-Ban-Reason / X-Ban-Until),随 persist 落盘,
    // 这样刷新页面后全屏遮罩仍能显示完整信息。banUntil 为 0 表示永久封禁。
    banReason: '',
    banUntil: 0,
    time: {
      socialPractice: 0,
      onCampus: 0,
      offCampus: 0
    },
    language: 'zh-CN',
    // 主题变体：设备缓存初始化，登录后从服务器同步覆盖
    theme: readStoredTheme()
  }),
  actions: {
    async getUserClassId(groups: string[]) {
      const groupsStore = useGroupsStore()
      const result = await Promise.all(groups.map((group) => groupsStore.fetchGroup(group)))
      const group = result.find((group) => group?.type === 'class')
      if (group) {
        this.class_id = group._id
      }
    },
    async setUserInformation(user: User | UserProfile) {
      this._id = user._id
      this.id = user.id
      this.name = user.name
      this.banned = Boolean((user as User).banned)
      if (Array.isArray(user.roles)) {
        this.groups = user.group ?? []
        this.class_id = user.class_id || ''
        this.position = user.roles
      } else {
        const legacy = user as User
        this.groups = legacy.group
        await this.getUserClassId(legacy.group)
        this.position = await getUserPositions(legacy)
      }
    },
    async setUser(user: string, password: string, options?: { suppressErrorToast?: boolean }) {
      const result = await api.user.auth.useLongTermAuth(
        user,
        password,
        'long',
        'login',
        options
      )
      if (result) {
        const information: User | UserProfile =
          result.profile ?? ((await api.user.readOne(result._id)) as User)
        await this.setUserInformation(information)
        this.shouldResetPassword = Boolean(result.weak_password)
        this.isLogin = true
        // 登录成功后同步账号主题偏好
        await this.syncThemeFromServer()
      }
    },
    async refreshUser() {
      const result = (await api.user.readOne_v3(this._id)) as User
      await this.setUserInformation(result)
    },
    async restoreSessionProfile(oid: string, token: string) {
      const profile = await api.user.readOne_v3(oid)
      if (localStorage.getItem('token') !== token) return
      await this.setUserInformation(profile)
      this.isLogin = true
    },
    getUser() {
      return {
        id: this.id,
        name: this.name
      }
    },
    async removeUser() {
      void api.user.auth.logout().catch(() => {})
      localStorage.removeItem('token')
      this._id = ''
      this.id = 0
      this.name = ''
      this.groups = []
      this.position = []
      this.class_id = ''
      this.isLogin = false
      this.shouldResetPassword = false
      this.banned = false
      // 封禁原因/到期必须跟着身份一起清掉,否则下一个登录者会看到上一个人的封禁文案。
      // bannedNoticeAt 的清理交给 App.vue 的 finalizeBan(倒计时结束才归零),这里不动。
      this.banReason = ''
      this.banUntil = 0
      this.time = { socialPractice: 0, onCampus: 0, offCampus: 0 }
      // 换人登录时必须丢掉上一个账号的响应缓存，避免串数据
      clearMemoryCache()
      await clearCache()
    },
    async getUserActivityTime() {
      const result = normalizeUserTime(await api.user.time.read(this._id))
      this.time.offCampus = result.offCampus
      this.time.onCampus = result.onCampus
      this.time.socialPractice = result.socialPractice
    },
    setLanguage(language: string) {
      this.language = language
    },
    async resetPassword(token: string, newPassword: string) {
      const result = await api.user.password.put(this._id, newPassword, token)
      if (!result) {
        return
      }
      ElNotification({
        title: '密码修改成功',
        message: '请重新登录',
        type: 'success'
      })
      await this.removeUser()
    },
    setTime(time: UserActivityTimeSums) {
      const normalized = normalizeUserTime(time)
      this.time.onCampus = normalized.onCampus
      this.time.offCampus = normalized.offCampus
      this.time.socialPractice = normalized.socialPractice
    },
    relatedGroup(group: string): boolean {
      if (this.position.includes('admin') || this.position.includes('department')) {
        return true
      } else if (this.position.includes('secretary')) {
        return group === this.class_id
      } else return false
    },
    validatePasswordStrength(pwd: string): boolean {
      let strength = 0
      if (!(/^[ -\x7e]{8,14}$/.test(pwd))) {
        return false
      }
      if (/^(?=.*[a-z])/.test(pwd)) {
        strength += 1
      }
      if (/^(?=.*[A-Z])/.test(pwd)) {
        strength += 1
      }
      if (/^(?=.*[0-9])/.test(pwd)) {
        strength += 1
      }
      if (/^(?=.*[^a-zA-Z0-9])/.test(pwd)) {
        strength += 1
      }
      return strength >= 3
    },
    /**
     * 立即应用主题偏好：落设备缓存并更新 store。
     * 主题是整站多构建，只有跳转才能真的换掉应用，所以这里只记账不跳转。
     */
    applyThemePreference(theme: string): string {
      this.theme = setStoredTheme(theme)
      return this.theme
    },
    /**
     * 从服务器同步账号主题偏好：登录后调用，失败静默（保留本地值）。
     * 若当前跑在根 canary 应用里而账号偏好是别的主题，整页跳转到那份完整应用；
     * 子应用（`/themes/<id>/`）不执行这段逻辑，避免互相踢。
     */
    async syncThemeFromServer() {
      try {
        if (new URLSearchParams(window.location.search).has('choose-theme')) return
        if (sessionStorage.getItem('zvms.theme-manual-return') === '1') {
          sessionStorage.removeItem('zvms.theme-manual-return')
          this.theme = readStoredTheme()
          return
        }
        const { theme } = await api.theme.getMyTheme()
        if (!theme) {
          return
        }
        const resolved = this.applyThemePreference(theme)
        if (typeof window === 'undefined' || !isRootApp(window.location.pathname)) {
          return
        }
        if (resolved === 'canary') return
        const response = await fetch('/themes.json', { headers: { Accept: 'application/json' } })
        if (!response.ok) return
        const manifest = (await response.json()) as { themes?: { id: string; base: string }[] }
        const target = themePath(resolved)
        if (manifest.themes?.some((item) => item.id === resolved && item.base === target)) {
          window.location.assign(target)
        }
      } catch {
        // 同步失败不影响登录流程，保留设备缓存的主题
      }
    }
  },
  persist: {
    storage: localStorage
  }
})

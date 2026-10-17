import api from '@/api'
import type { User, UserPosition, UserProfile, UserActivityTimeSums } from '@/../types'
import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus'
import { useRouter } from 'vue-router'
import { getUserPositions } from '@/utils/groupPosition'

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
    time: {
      socialPractice: 0,
      onCampus: 0,
      offCampus: 0
    },
    language: 'zh-CN'
  }),
  actions: {
    async getUserClassId(groups: string[]) {
      const result = await Promise.all(groups.map((group) => api.group.readOne(group)))
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
    async setUser(user: string, password: string) {
      const result = await api.user.auth.useLongTermAuth(user, password, 'long', 'login')
      if (result) {
        const information: User | UserProfile =
          result.profile ?? ((await api.user.readOne(result._id)) as User)
        await this.setUserInformation(information)
        if (result.weak_password || !this.validatePasswordStrength(password)) {
          this.shouldResetPassword = true
        }
        this.isLogin = true
      }
    },
    async refreshUser() {
      const result = (await api.user.readOne_v3(this._id)) as User
      await this.setUserInformation(result)
    },
    getUser() {
      return {
        id: this.id,
        name: this.name
      }
    },
    async removeUser() {
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
  },
  persist: {
    storage: localStorage
  }
})

//import api from '@/api'
//import { login } from '@/services/platform'
import type { User, UserPosition } from '@/types'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { pad, getXuehaiId } from '@/plugins/ua.ts'
//import { getXHProfile } from '@/api/xh.js'

export const useUserStore = defineStore('user', {
  state: () => ({
    id: 0,
    _id: '',
    name: '',
    position: [] as UserPosition[],
    class_id: '',
    class_name: '',
    isLogin: false,
    time: {
      socialPractice: 0,
      onCampus: 0,
      offCampus: 0,
      loaded: false,
    },
    verifiedId: '',
    verifiedName: '',
    shouldVerify: true,
  }),
  actions: {
    async getUserClassId(class_id?: string) {
      const group = await (await import('@/api')).group.readOne_v2(class_id)
      if (group) {
        this.class_id = group._id
        this.class_name = group.name
      }
    },
    async setUserInformation(user: User) {
      this._id = user._id
      this.id = user.id
      this.name = user.name
      await this.getUserClassId(user.class_id)
      this.position = user.roles
    },
    async setUser(user: string, password: string, new_api: boolean = true) {
      const { login } = await import('@/services/platform')
      let result = await login({
          username: user,
          password,
          verifiedName: this.verifiedName,
          verifiedId: this.verifiedId,
          loginType: 'password'
      })
      if (!result.token) {
          result = await (await import('@/dialogs')).modifyPasswordDialogs('', '', async (_, pwd) => await login({
              username: user,
              password,
              newPassword: pwd,
              verifiedName: this.verifiedName,
              verifiedId: this.verifiedId,
              loginType: 'password'
          }), 'dummy_value')
      }
      if (result?.token) {
        const information = result.profile
        if (this.shouldVerify && !result.otpauth) {
          if (pad()) {
            try {
              const username = this.verifiedName || (this.verifiedName = window.webView?.getUserName?.() ?? (await (await import('@/api')).xh.getXHProfile(getXuehaiId())).username)
              if (this.verifiedId && information._id !== this.verifiedId || information.name !== username?.replaceAll?.(/[0-9A-Za-z]/g, '')) {
                ElMessage({
                  type: 'error',
                  message: `这是${username}的设备，请勿使用他人设备`,
                  plain: true,
                  grouping: true,
                })
                this.removeUser()
                throw new Error('请勿使用他人设备')
              }
              this.verifiedId = information._id
            } catch (ex) {
              this.removeUser()
              throw ex
            }
          }
          else this.shouldVerify = false
        }
        await this.setUserInformation(information)
        this.isLogin = true
      } else {
          // TODO 登录失败提示
      }
    },
    getUser() {
      return {
        id: this.id,
        name: this.name
      }
    },
    removeUser() {
      this._id = ''
      this.id = 0
      this.name = ''
      this.groups = []
      this.position = []
      this.class_id = ''
      this.class_name = ''
      this.isLogin = false
      this.shouldResetPassword = false
      this.time.onCampus = 0
      this.time.offCampus = 0
      this.time.socialPractice = 0
      this.time.loaded = false
      localStorage.removeItem('token')
    },
    async getUserActivityTime() {
      const result = (await (await import('@/api')).user.time.read(this._id)) as unknown as {
        'off-campus': number
        'on-campus': number
        'social-practice': number
      }
      this.time.offCampus = result['off-campus']
      this.time.onCampus = result['on-campus']
      this.time.socialPractice = result['social-practice']
      this.time.loaded = true
      return result
    },
    async resetPassword(token: string, newPassword: string) {
      const result = await (await import('@/api')).user.password.put_v2(this._id, newPassword, token)
      ElMessage({
        title: '密码修改成功，请重新登录',
        type: 'success'
      })
      this.removeUser()
    },
    /*setTime(time: { onCampus: number; offCampus: number; socialPractice: number }) {
      this.time.onCampus = time.onCampus
      this.time.offCampus = time.offCampus
      this.time.socialPractice = time.socialPractice
    },*/
  },
  persist: {
    storage: localStorage
  }
})

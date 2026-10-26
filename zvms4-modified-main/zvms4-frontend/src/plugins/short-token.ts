import { ElMessageBox } from 'element-plus'
import api from '@/api'
import i18n from '@/i18n'

export function temporaryToken(userid: string, reason?: string): Promise<string> {
  const locale = i18n.global.locale.value
  const locales = {
    'zh-CN': {
      title: '请输入密码',
      // 解锁场景由调用方给更具体的提示（如平板访问附件），默认保持原来的问法。
      message: reason || '请确认您的密码。',
      confirm: '确认',
      cancel: '取消',
      validation: ''
    },
    'en-US': {
      title: '',
      message: '',
      confirm: '',
      cancel: '',
      validation: ''
    }
  }
  return new Promise((resolve, reject) => {
    ElMessageBox.prompt(locales[locale].message, locales[locale].title, {
      confirmButtonText: locales[locale].confirm,
      cancelButtonText: locales[locale].cancel,
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

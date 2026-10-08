import { ElMessageBox } from 'element-plus'
import { login } from '@/services/platform'

export function temporaryToken(userid: string): Promise<string> {
  return new Promise((resolve, reject) => {
      ElMessageBox.prompt('请确认您的密码。', '身份验证', {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        inputType: 'password',
        type: 'warning',
        showClose: false,
        closeOnClickModal: false,
        closeOnPressEscape: false,
      })
      .then(({ value }) => {
        login({
          user: userid,
          password: value,
          loginType: 'password_authz'
        })
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

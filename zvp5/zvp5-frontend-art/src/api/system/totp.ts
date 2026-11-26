import axios from '@/plugins/axios.ts'

// 登录动态码(TOTP):仅允许查看本人(非本人 403,用户不存在 404)
// 每次查看后端会记录一条 totp_view 操作日志

export interface TotpResponse {
  status: string
  code: number
  data: {
    /** 当前动态码,固定 6 位数字(不足位零填充) */
    otp: string
    /** 动态码过期时刻,Unix 秒 */
    exp: number
  }
}

// 读取自己的当前登录动态码(user_id 传本人 ObjectId,即 user store 的 _id)
export function getMyTotp(userId: string) {
  return axios.get<TotpResponse>(`/v1/users/${userId}/totp`)
}
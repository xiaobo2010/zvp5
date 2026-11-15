import type { UserPosition } from './user'

export interface UserProfile {
  _id: string
  id: string
  name: string
  roles: UserPosition[]
  class_id: string
  group?: string[]
}

export interface LoginResult {
  token: string // JSON Web Token
  _id: string // MongoDB ObjectID
  profile?: UserProfile
  weak_password?: boolean // set when the server only issued a temporary token due to a weak password
  binding_mismatch?: boolean // 本机设备与主设备不一致（仅观测，不拦截）
  temp_session?: boolean // 临时密码兑换出的 1 小时会话
}
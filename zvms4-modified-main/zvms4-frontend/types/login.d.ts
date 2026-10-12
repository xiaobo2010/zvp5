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
}
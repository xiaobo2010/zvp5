export interface Log {
  _id: string
  url: string
  user: string
  user_name?: string
  type?: string
  level?: string
  data: string
  timestamp: number
}
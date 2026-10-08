export interface SuccessResponse<T> {
  status: 'ok'
  data: T
  code: number
}

export type Response<T> = SuccessResponse<T>

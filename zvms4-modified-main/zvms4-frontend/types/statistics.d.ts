import type { User, UserActivityTimeSums } from './user'

/**
 * @description A user's volunteer duration row returned by the time/statistics
 * endpoints. Duration fields stay kebab-case to match the backend payload.
 */
export interface ActivityTimeRecord extends UserActivityTimeSums {
  _id: string
  name: string
  id: string
}

/**
 * @description Payload shape of `/v2/groups/:id/time`.
 */
export interface ActivityTimePage {
  data: ActivityTimeRecord[]
  metadata: {
    size: number
  }
}

/**
 * @description Payload shape of `/v2/groups/:id/users`.
 */
export interface GroupUsersPage {
  members: User[]
  total: number
}

/**
 * @description Payload shape of `/v2/statistics/activities/:id/description`.
 */
export interface ActivityStatisticsDescription {
  [key: string]: number
}

/**
 * @description Payload shape of `/v2/statistics/activities/:id/layers`.
 */
export interface ActivityStatisticsLayer {
  value: number
  count: number
}

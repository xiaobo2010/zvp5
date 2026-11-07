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
 * @description Payload shape of `/v1/groups/:id/time`.
 */
export interface ActivityTimePage {
  data: ActivityTimeRecord[]
  metadata: {
    size: number
  }
}

/**
 * @description Payload shape of `/v1/groups/:id/users`.
 */
export interface GroupUsersPage {
  members: User[]
  total: number
}

/**
 * @description Payload shape of `/v1/statistics/activities/:id/description`.
 * The keys mirror the backend `util/activity_statistics.describe_durations`
 * output verbatim, so a typo in a consumer becomes a type error.
 */
export interface ActivityStatisticsDescription {
  mean: number
  median: number
  mode: number
  std: number
  min: number
  max: number
  var: number
  sum: number
  '25_percentile': number
  '75_percentile': number
  total: number
}

/**
 * @description A single duration bucket of the duration histogram, `[from, to)`.
 */
export interface ActivityStatisticsBin {
  from: number
  to: number
  count: number
}

/**
 * @description Per-mode aggregate row of the activity analysis payload.
 */
export interface ActivityStatisticsModeGroup {
  mode: string
  count: number
  sum: number
  mean: number
}

/**
 * @description Per-class aggregate row of the activity analysis payload.
 */
export interface ActivityStatisticsClassGroup {
  group_oid: string
  group_name: string
  count: number
  sum: number
  mean: number
}

/**
 * @description Minimal member reference reused by optional roster fields of the
 * statistics payloads. Mirrors the `{_id, id, name}` tuples already carried by
 * activity member payloads; `duration` is optional and measured in hours.
 */
export interface StatisticsMemberRef {
  _id: string
  id: string
  name: string
  duration?: number
}

/**
 * @description Payload shape of `/v1/statistics/activities/:id/analysis`.
 */
export interface ActivityStatistics {
  total: number
  bin_width: number
  description: ActivityStatisticsDescription | null
  bins: ActivityStatisticsBin[]
  modes: ActivityStatisticsModeGroup[]
  classes: ActivityStatisticsClassGroup[]
  /**
   * @description Optional member rosters per participation mode. Keys are mode
   * names and entries mirror the `modes` rows one to one, so consumers can join
   * by `mode`. When the field is absent the UI degrades to aggregate rows only.
   */
  mode_members?: Record<string, StatisticsMemberRef[]>
}

/**
 * @description Member rosters carried by the additive `member_rosters` field of
 * `/v1/groups/:id/statistics/compliance`. Mode keys and bucket keys match the
 * compliance payload verbatim so consumers can join by label; any missing mode
 * or bucket means no visible members for that slice, and a response without the
 * field degrades the same way.
 */
export interface GroupComplianceMemberRosters {
  [mode: string]: Record<string, StatisticsMemberRef[]>
}

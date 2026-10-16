import type { ActivityStatistics, ActivityStatisticsClassGroup } from '@/../types'

function formatTick(value: number): string {
  return String(Math.round(value * 1e6) / 1e6)
}

/**
 * @description Render a histogram bucket range as `from–to`, using an en dash
 * (`U+2013`) as the separator. Integer bounds carry no decimal point.
 */
export function formatBinLabel(from: number, to: number): string {
  return `${formatTick(from)}–${formatTick(to)}`
}

/**
 * @description File name used when exporting an activity's statistics as CSV.
 */
export function statisticsFileName(aid: string): string {
  return `activity-${aid}-statistics.csv`
}

export interface StatisticsCsvLabels {
  overview: string
  metric: string
  value: string
  total: string
  sum: string
  mean: string
  median: string
  min: string
  max: string
  p25: string
  p75: string
  distribution: string
  range: string
  count: string
  modeGroup: string
  classGroup: string
  mode: string
  className: string
  unnamedClass: string
  modeNames: Record<string, string>
}

function escapeCsvField(field: string): string {
  if (/[",\r\n]/.test(field)) {
    return `"${field.replace(/"/g, '""')}"`
  }

  return field
}

function csvRow(...fields: (string | number)[]): string {
  return fields.map((field) => escapeCsvField(String(field))).join(',')
}

/**
 * @description Build the CSV export of an activity analysis payload. The output
 * starts with a UTF-8 BOM, uses CRLF line endings, and contains four sections
 * (overview, distribution, mode groups, class groups) separated by blank lines.
 */
export function buildStatisticsCsv(stats: ActivityStatistics, labels: StatisticsCsvLabels): string {
  const overview = [labels.overview, csvRow(labels.metric, labels.value)]
  if (stats.description) {
    const description = stats.description
    overview.push(
      csvRow(labels.total, description.total),
      csvRow(labels.sum, description.sum),
      csvRow(labels.mean, description.mean),
      csvRow(labels.median, description.median),
      csvRow(labels.min, description.min),
      csvRow(labels.max, description.max),
      csvRow(labels.p25, description['25_percentile']),
      csvRow(labels.p75, description['75_percentile'])
    )
  }

  const distribution = [labels.distribution, csvRow(labels.range, labels.count)]
  for (const bin of stats.bins) {
    distribution.push(csvRow(formatBinLabel(bin.from, bin.to), bin.count))
  }

  const modeGroup = [labels.modeGroup, csvRow(labels.mode, labels.count, labels.sum)]
  for (const group of stats.modes) {
    modeGroup.push(csvRow(labels.modeNames[group.mode] ?? group.mode, group.count, group.sum))
  }

  const classGroup = [labels.classGroup, csvRow(labels.className, labels.count, labels.sum)]
  for (const group of stats.classes) {
    const name = group.group_name === '' ? labels.unnamedClass : group.group_name
    classGroup.push(csvRow(name, group.count, group.sum))
  }

  return (
    '\uFEFF' +
    [
      overview.join('\r\n'),
      distribution.join('\r\n'),
      modeGroup.join('\r\n'),
      classGroup.join('\r\n')
    ].join('\r\n\r\n')
  )
}

/**
 * @description Cap the class ranking chart at `limit` rows. `rows` is expected to
 * be already ordered by the backend, so its order is preserved; when it holds
 * more than `limit` rows the surplus is folded into a trailing "other" row.
 */
export function mergeClassRows(
  rows: ActivityStatisticsClassGroup[],
  limit: number,
  otherLabel: string
): ActivityStatisticsClassGroup[] {
  const visible = rows.slice(0, limit)
  const rest = rows.slice(limit)
  if (rest.length === 0) {
    return visible
  }
  const count = rest.reduce((acc, row) => acc + row.count, 0)
  const sum = rest.reduce((acc, row) => acc + row.sum, 0)
  visible.push({
    group_oid: 'other',
    group_name: otherLabel,
    count,
    sum,
    mean: count > 0 ? sum / count : 0
  })
  return visible
}

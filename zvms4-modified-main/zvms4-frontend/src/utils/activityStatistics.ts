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
  unknownClass: string
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
    const name =
      group.group_oid === ''
        ? labels.unnamedClass
        : group.group_name || `${labels.unknownClass}（${group.group_oid}）`
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
 * @description Order every class row for the ranking chart without folding
 * named classes into an "other" bucket. Named classes sort by total duration
 * ascending; class-less users and unresolved class records stay pinned last.
 */
export function orderClassRowsForChart(
  rows: ActivityStatisticsClassGroup[]
): ActivityStatisticsClassGroup[] {
  const named = rows.filter((row) => row.group_oid !== '' && row.group_name !== '')
  const pinned = rows.filter((row) => row.group_oid === '' || row.group_name === '')
  named.sort((a, b) => a.sum - b.sum || a.group_name.localeCompare(b.group_name))
  return [...named, ...pinned]
}

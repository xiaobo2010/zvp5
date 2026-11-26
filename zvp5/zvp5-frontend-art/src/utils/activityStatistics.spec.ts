import { describe, expect, it } from 'vitest'
import {
  buildStatisticsCsv,
  formatBinLabel,
  orderClassRowsForChart,
  statisticsFileName,
  type StatisticsCsvLabels
} from './activityStatistics'
import type { ActivityStatistics, ActivityStatisticsClassGroup } from '@/../types'

const labels: StatisticsCsvLabels = {
  overview: '概览',
  metric: '指标',
  value: '数值',
  total: '参与人数',
  sum: '总时长',
  mean: '平均值',
  median: '中位数',
  min: '最小值',
  max: '最大值',
  p25: '25% 分位数',
  p75: '75% 分位数',
  distribution: '时长分布',
  range: '区间',
  count: '人数',
  modeGroup: '分组-模式',
  classGroup: '分组-班级',
  mode: '参与模式',
  className: '班级',
  unnamedClass: '未分班',
  unknownClass: '未知班级',
  modeNames: { 'on-campus': '校内义工' }
}

const fixture: ActivityStatistics = {
  total: 42,
  bin_width: 0.5,
  description: {
    total: 42,
    sum: 63.5,
    mean: 1.51,
    median: 1.5,
    mode: 1.5,
    std: 0.9,
    var: 0.81,
    min: 0,
    max: 4,
    '25_percentile': 1,
    '75_percentile': 2
  },
  bins: [
    { from: 0, to: 0.5, count: 3 },
    { from: 0.5, to: 1, count: 0 },
    { from: 1, to: 2, count: 5 }
  ],
  modes: [
    { mode: 'on-campus', count: 10, sum: 12.5, mean: 1.25 },
    { mode: 'hybrid', count: 2, sum: 3, mean: 1.5 }
  ],
  classes: [
    { group_oid: 'g1', group_name: '高一(1)班', count: 5, sum: 6, mean: 1.2 },
    { group_oid: 'g2', group_name: '', count: 1, sum: 0.5, mean: 0.5 }
  ]
}

describe('formatBinLabel', () => {
  it('omits the decimal point for integer bounds', () => {
    expect(formatBinLabel(0, 0.5)).toBe('0–0.5')
    expect(formatBinLabel(1, 2)).toBe('1–2')
    expect(formatBinLabel(2, 4)).toBe('2–4')
  })

  it('keeps decimals for fractional bounds', () => {
    expect(formatBinLabel(0.5, 1)).toBe('0.5–1')
    expect(formatBinLabel(1.5, 2.5)).toBe('1.5–2.5')
  })

  it('always returns a string joined by an en dash (U+2013)', () => {
    const result = formatBinLabel(0, 1)
    expect(typeof result).toBe('string')
    expect(result.charCodeAt(1)).toBe(0x2013)
  })
})

describe('statisticsFileName', () => {
  it('appends the activity id', () => {
    expect(statisticsFileName('abc')).toBe('activity-abc-statistics.csv')
    expect(statisticsFileName('a1b2c3')).toBe('activity-a1b2c3-statistics.csv')
  })
})

describe('buildStatisticsCsv', () => {
  it('serializes the full payload with BOM, CRLF and blank-line sections', () => {
    const expected =
      '\uFEFF概览\r\n' +
      '指标,数值\r\n' +
      '参与人数,42\r\n' +
      '总时长,63.5\r\n' +
      '平均值,1.51\r\n' +
      '中位数,1.5\r\n' +
      '最小值,0\r\n' +
      '最大值,4\r\n' +
      '25% 分位数,1\r\n' +
      '75% 分位数,2\r\n' +
      '\r\n' +
      '时长分布\r\n' +
      '区间,人数\r\n' +
      '0–0.5,3\r\n' +
      '0.5–1,0\r\n' +
      '1–2,5\r\n' +
      '\r\n' +
      '分组-模式\r\n' +
      '参与模式,人数,总时长\r\n' +
      '校内义工,10,12.5\r\n' +
      'hybrid,2,3\r\n' +
      '\r\n' +
      '分组-班级\r\n' +
      '班级,人数,总时长\r\n' +
      '高一(1)班,5,6\r\n' +
      '未知班级（g2）,1,0.5'

    const csv = buildStatisticsCsv(fixture, labels)

    expect(csv).toBe(expected)
    expect(csv.startsWith('\uFEFF')).toBe(true)
    expect(/(^|[^\r])\n/.test(csv)).toBe(false)
  })

  it('escapes class names containing commas and quotes', () => {
    const stats: ActivityStatistics = {
      total: 2,
      bin_width: 0.5,
      description: {
        total: 2,
        sum: 2,
        mean: 1,
        median: 0.5,
        mode: 1,
        std: 0.5,
        var: 0.25,
        min: 0,
        max: 3,
        '25_percentile': 1,
        '75_percentile': 2
      },
      bins: [{ from: 0, to: 0.5, count: 1 }],
      modes: [{ mode: 'on-campus', count: 2, sum: 2, mean: 1 }],
      classes: [
        { group_oid: 'a', group_name: '李四, 高一(2)"班"', count: 1, sum: 1, mean: 1 },
        { group_oid: 'b', group_name: '', count: 1, sum: 1, mean: 1 }
      ]
    }

    const expected =
      '\uFEFF概览\r\n' +
      '指标,数值\r\n' +
      '参与人数,2\r\n' +
      '总时长,2\r\n' +
      '平均值,1\r\n' +
      '中位数,0.5\r\n' +
      '最小值,0\r\n' +
      '最大值,3\r\n' +
      '25% 分位数,1\r\n' +
      '75% 分位数,2\r\n' +
      '\r\n' +
      '时长分布\r\n' +
      '区间,人数\r\n' +
      '0–0.5,1\r\n' +
      '\r\n' +
      '分组-模式\r\n' +
      '参与模式,人数,总时长\r\n' +
      '校内义工,2,2\r\n' +
      '\r\n' +
      '分组-班级\r\n' +
      '班级,人数,总时长\r\n' +
      '"李四, 高一(2)""班""",1,1\r\n' +
      '未知班级（b）,1,1'

    expect(buildStatisticsCsv(stats, labels)).toBe(expected)
  })

  it('emits only the overview header when description is null', () => {
    const stats: ActivityStatistics = {
      total: 0,
      bin_width: 0,
      description: null,
      bins: [],
      modes: [],
      classes: []
    }

    const expected =
      '\uFEFF概览\r\n' +
      '指标,数值\r\n' +
      '\r\n' +
      '时长分布\r\n' +
      '区间,人数\r\n' +
      '\r\n' +
      '分组-模式\r\n' +
      '参与模式,人数,总时长\r\n' +
      '\r\n' +
      '分组-班级\r\n' +
      '班级,人数,总时长'

    expect(buildStatisticsCsv(stats, labels)).toBe(expected)
  })

  it('keeps every section header when all collections are empty', () => {
    const stats: ActivityStatistics = {
      total: 0,
      bin_width: 0,
      description: {
        total: 0,
        sum: 0,
        mean: 0,
        median: 0,
        mode: 0,
        std: 0,
        var: 0,
        min: 0,
        max: 0,
        '25_percentile': 0,
        '75_percentile': 0
      },
      bins: [],
      modes: [],
      classes: []
    }

    const expected =
      '\uFEFF概览\r\n' +
      '指标,数值\r\n' +
      '参与人数,0\r\n' +
      '总时长,0\r\n' +
      '平均值,0\r\n' +
      '中位数,0\r\n' +
      '最小值,0\r\n' +
      '最大值,0\r\n' +
      '25% 分位数,0\r\n' +
      '75% 分位数,0\r\n' +
      '\r\n' +
      '时长分布\r\n' +
      '区间,人数\r\n' +
      '\r\n' +
      '分组-模式\r\n' +
      '参与模式,人数,总时长\r\n' +
      '\r\n' +
      '分组-班级\r\n' +
      '班级,人数,总时长'

    expect(buildStatisticsCsv(stats, labels)).toBe(expected)
  })
})

describe('orderClassRowsForChart', () => {
  const classRow = (index: number): ActivityStatisticsClassGroup => ({
    group_oid: `g${index}`,
    group_name: `班级${index}`,
    count: index,
    sum: index / 2,
    mean: index > 0 ? 0.5 : 0
  })

  it('keeps every named class instead of folding extra classes into other', () => {
    const rows = Array.from({ length: 13 }, (_, index) => classRow(index + 1))

    const ordered = orderClassRowsForChart(rows)

    expect(ordered).toHaveLength(13)
    expect(ordered.map((row) => row.group_oid)).not.toContain('other')
    expect(new Set(ordered.map((row) => row.group_oid))).toEqual(
      new Set(rows.map((row) => row.group_oid))
    )
  })

  it('sorts named classes by total duration and pins class-less rows last', () => {
    const rows = [
      classRow(5),
      { group_oid: '', group_name: '', count: 9, sum: 99, mean: 11 },
      classRow(1),
      classRow(3)
    ]

    const ordered = orderClassRowsForChart(rows)

    expect(ordered.map((row) => row.group_oid)).toEqual(['g1', 'g3', 'g5', ''])
  })

  it('keeps unresolved class records separate from class-less users', () => {
    const rows = [
      { group_oid: 'missing', group_name: '', count: 1, sum: 2, mean: 2 },
      { group_oid: '', group_name: '', count: 1, sum: 1, mean: 1 }
    ]
    expect(orderClassRowsForChart(rows)).toEqual(rows)
  })

  it('returns an empty array for an empty input', () => {
    expect(orderClassRowsForChart([])).toEqual([])
  })
})

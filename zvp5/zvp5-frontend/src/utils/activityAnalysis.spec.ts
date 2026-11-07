import { describe, expect, it } from 'vitest'
import type { ActivityStatistics } from '@/../types'
import type { Activity } from '@/../types/v2'
import {
  ACTIVITY_CONTEXT_LIMIT,
  ACTIVITY_CONTEXT_MISSING,
  ACTIVITY_CONTEXT_TRUNCATED,
  buildActivityContextMarkdown
} from './activityAnalysis'

function activity(overrides: Partial<Activity> = {}): Activity {
  return {
    _id: 'act-1',
    type: 'on-campus',
    name: '图书馆整理',
    description: '把捐赠图书按学科分类上架。',
    date: '2026-10-20 08:30:00',
    createdAt: '2026-10-01 10:00:00',
    updatedAt: '2026-10-02 10:00:00',
    appointee: 'u2',
    approver: 'authority',
    creator: 'u1',
    status: 'effective',
    place: '图书馆三层',
    origin: 'labor',
    ...overrides
  }
}

function statistics(overrides: Partial<ActivityStatistics> = {}): ActivityStatistics {
  return {
    total: 24,
    bin_width: 1,
    description: {
      mean: 1.2,
      median: 1,
      mode: 1,
      std: 0.4,
      min: 0.5,
      max: 3,
      var: 0.16,
      sum: 28.8,
      '25_percentile': 1,
      '75_percentile': 1.5,
      total: 24
    },
    bins: [],
    modes: [],
    classes: [
      { group_oid: 'g1', group_name: '计科2201', count: 10, sum: 12, mean: 1.2 },
      { group_oid: 'g2', group_name: '软件2202', count: 8, sum: 9, mean: 1.125 }
    ],
    ...overrides
  }
}

describe('活动 AI 分析上下文', () => {
  it('按标识、基本信息、聚合统计、描述四段输出', () => {
    const md = buildActivityContextMarkdown({
      activity: activity(),
      membersCount: 30,
      creatorName: '张三',
      statistics: statistics()
    })

    expect(md).toContain('# 活动标识')
    expect(md).toContain('- OID：act-1')
    expect(md).toContain('- 类型：校内义工')
    expect(md).toContain('- 状态：有效')
    expect(md).toContain('## 基本信息')
    expect(md).toContain('- 地点：图书馆三层')
    expect(md).toContain('- 组织人：张三（u1）')
    expect(md).toContain('## 聚合统计')
    expect(md).toContain('- 报名数：30')
    expect(md).toContain('- 签到数（有效参与）：24')
    expect(md).toContain('- 出勤率：80%')
    expect(md).toContain('| 计科2201 | 10 | 12 |')
    expect(md).toContain('## 活动描述')
    expect(md).toContain('把捐赠图书按学科分类上架。')
  })

  it('标识置顶，描述在最后', () => {
    const md = buildActivityContextMarkdown({ activity: activity() })
    expect(md.indexOf('# 活动标识')).toBeLessThan(md.indexOf('## 基本信息'))
    expect(md.indexOf('## 基本信息')).toBeLessThan(md.indexOf('## 聚合统计'))
    expect(md.indexOf('## 聚合统计')).toBeLessThan(md.indexOf('## 活动描述'))
  })

  it('不放成员名单', () => {
    const md = buildActivityContextMarkdown({
      activity: activity(),
      membersCount: 30,
      statistics: statistics({
        mode_members: { 'on-campus': [{ _id: 'm1', id: '2024001', name: '李四' }] }
      })
    })
    expect(md).not.toContain('李四')
    expect(md).not.toContain('2024001')
  })

  it('统计缺失时写占位，不写 0', () => {
    const md = buildActivityContextMarkdown({ activity: activity() })
    expect(md).toContain(`- 签到数（有效参与）：${ACTIVITY_CONTEXT_MISSING}`)
    expect(md).toContain(`- 出勤率：${ACTIVITY_CONTEXT_MISSING}`)
    expect(md).toContain(`- 报名数：${ACTIVITY_CONTEXT_MISSING}`)
  })

  it('描述超长时先砍描述，标识与统计保持完整', () => {
    const md = buildActivityContextMarkdown({
      activity: activity({ description: '长'.repeat(20000) }),
      membersCount: 30,
      statistics: statistics()
    })

    expect(md.length).toBeLessThanOrEqual(ACTIVITY_CONTEXT_LIMIT)
    expect(md).toContain(ACTIVITY_CONTEXT_TRUNCATED)
    expect(md).toContain('- OID：act-1')
    expect(md).toContain('- 签到数（有效参与）：24')
    expect(md).toContain('| 计科2201 | 10 | 12 |')
  })

  it('截断不劈开代理对', () => {
    const md = buildActivityContextMarkdown({
      activity: activity({ description: '😀'.repeat(20000) })
    })
    expect(md.length).toBeLessThanOrEqual(ACTIVITY_CONTEXT_LIMIT)
    expect(
      /(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(md)
    ).toBe(false)
  })

  it('班级多时只列前若干行，并写明省略', () => {
    const classes = Array.from({ length: 20 }, (_, index) => ({
      group_oid: `g${index}`,
      group_name: `班级${index}`,
      count: 20 - index,
      sum: 10 - index,
      mean: 1
    }))
    const md = buildActivityContextMarkdown({
      activity: activity(),
      statistics: statistics({ classes })
    })

    expect(md).toContain('| 班级0 | 20 | 10 |')
    expect(md).not.toContain('| 班级19 |')
    expect(md).toContain('仅列出人数最多的')
  })
})

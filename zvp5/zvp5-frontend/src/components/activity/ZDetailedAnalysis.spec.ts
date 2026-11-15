import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'
import i18n, { ensureLocaleForPath } from '@/i18n'
import ZDetailedAnalysis from './ZDetailedAnalysis.vue'
import type { Activity } from '@/../types/v2'

const { readMock } = vi.hoisted(() => ({ readMock: vi.fn() }))

vi.mock('@/api', () => ({
  default: {
    user: {
      activity: { read: readMock }
    }
  }
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    _id: 'user-oid',
    position: [],
    time: { onCampus: 0, offCampus: 0, socialPractice: 0 }
  })
}))

type ActivityRow = Activity & {
  mine: {
    _id: string
    member: string
    status: 'effective' | 'pending' | 'refused'
    mode: 'on-campus' | 'off-campus' | 'social-practice'
    duration: number
  }
}

function row(overrides: Partial<ActivityRow>): ActivityRow {
  return {
    _id: 'activity-oid',
    type: 'on-campus',
    name: '灵通记者团',
    description: '',
    date: '2026-09-01T00:00:00.000Z',
    createdAt: '2026-09-01T00:00:00.000Z',
    updatedAt: '2026-09-01T00:00:00.000Z',
    appointee: '',
    approver: 'authority',
    creator: 'creator-oid',
    status: 'effective',
    place: '',
    origin: 'activities',
    mine: {
      _id: 'member-oid',
      member: 'user-oid',
      status: 'effective',
      mode: 'on-campus',
      duration: 25
    },
    ...overrides
  }
}

function mountAnalysis(): VueWrapper {
  return mount(ZDetailedAnalysis, {
    props: {
      id: 'user-oid',
      mode: 'on-campus',
      sums: { 'on-campus': 10, 'off-campus': 0, 'social-practice': 0 }
    },
    global: { plugins: [i18n, ElementPlus] }
  })
}

// activity.* 字典按路由懒加载，组件测试不经过路由守卫，先按 /activities 装载一次。
beforeAll(async () => {
  await ensureLocaleForPath('/activities')
})

beforeEach(() => {
  readMock.mockReset()
})

describe('ZDetailedAnalysis', () => {
  it('marks a pending activity as 审核中 instead of 有效', async () => {
    readMock.mockResolvedValue({
      activities: [row({ status: 'pending', mine: { ...row({}).mine, status: 'effective' } })],
      total: 1
    })

    const wrapper = mountAnalysis()
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('审核中')
    expect(rows[0].text()).not.toContain('有效')

    wrapper.unmount()
  })

  it('keeps an effective activity marked as 有效', async () => {
    readMock.mockResolvedValue({ activities: [row({})], total: 1 })

    const wrapper = mountAnalysis()
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('有效')

    wrapper.unmount()
  })

  it('weakens the duration of a pending row and explains that it is not counted yet', async () => {
    readMock.mockResolvedValue({
      activities: [
        row({ status: 'pending' }),
        row({ _id: 'other-oid', status: 'effective' })
      ],
      total: 2
    })

    const wrapper = mountAnalysis()
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows[0].find('.duration-cell--pending').exists()).toBe(true)
    expect(rows[1].find('.duration-cell--pending').exists()).toBe(false)

    const note = wrapper.find('.pending-note')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('1 项')
    expect(note.text()).toContain('尚未计入累计时长')

    wrapper.unmount()
  })

  it('hides the note when every activity is already effective', async () => {
    readMock.mockResolvedValue({ activities: [row({})], total: 1 })

    const wrapper = mountAnalysis()
    await flushPromises()

    expect(wrapper.find('.pending-note').exists()).toBe(false)

    wrapper.unmount()
  })
})

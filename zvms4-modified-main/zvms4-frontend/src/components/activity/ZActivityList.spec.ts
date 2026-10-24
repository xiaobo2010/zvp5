import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'
import i18n from '@/i18n'
import ZActivityList from './ZActivityList.vue'
import type { Activity } from '@/../types/v2'

const { mineMock } = vi.hoisted(() => ({ mineMock: vi.fn() }))

vi.mock('@/api', () => ({
  default: {
    activity: {
      read: { mine: mineMock }
    }
  }
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    _id: 'user-oid',
    position: [],
    class_id: '',
    time: { onCampus: 0, offCampus: 0, socialPractice: 0 }
  })
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRoute: () => ({ query: {} }),
    useRouter: () => ({ replace: vi.fn(), push: vi.fn() })
  }
})

type ActivityRow = Activity & {
  mine: {
    _id: string
    member: string
    status: 'effective' | 'pending' | 'refused'
    mode: 'on-campus' | 'off-campus' | 'social-practice'
    duration: number
  }
}

function row(overrides: Partial<ActivityRow> = {}): ActivityRow {
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

function mountList(): VueWrapper {
  return mount(ZActivityList, {
    props: { role: 'mine' },
    global: { plugins: [i18n, ElementPlus] }
  })
}

// element-plus 的 auto 布局表格会在挂载后用 setTimeout 量列宽,过早卸载会让
// 回调拿到已销毁的 thead 引用;先让定时器跑完再卸载。
async function unmount(wrapper: VueWrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0))
  wrapper.unmount()
}

beforeEach(() => {
  mineMock.mockReset()
})

describe('ZActivityList', () => {
  it('shows the status of a pending activity in the 我的活动 list', async () => {
    mineMock.mockResolvedValue({
      activities: [row({ status: 'pending' }), row({ _id: 'other-oid' })],
      total: 2
    })

    const wrapper = mountList()
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('.mine-status').text()).toContain('审核中')
    expect(rows[1].find('.mine-status').text()).toContain('有效')

    await unmount(wrapper)
  })

  it('does not render a pending activity as 有效', async () => {
    mineMock.mockResolvedValue({ activities: [row({ status: 'pending' })], total: 1 })

    const wrapper = mountList()
    await flushPromises()

    const status = wrapper.find('.mine-status')
    expect(status.text()).toContain('审核中')
    expect(status.text()).not.toContain('有效')

    await unmount(wrapper)
  })
})

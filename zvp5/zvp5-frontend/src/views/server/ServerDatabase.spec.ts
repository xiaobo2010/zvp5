import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import ServerDatabase from './ServerDatabase.vue'

const { elementComponents, elMessageSpy } = vi.hoisted(() => {
  const make = (name: string, template = '<div><slot /><slot name="header" /></div>') => ({
    name,
    template
  })
  return {
    elementComponents: {
      ElCard: make('ElCard'),
      ElTag: make('ElTag', '<span><slot /></span>'),
      ElAlert: make('ElAlert', '<div><slot />{{ title }}</div>'),
      ElButton: make('ElButton', '<button :disabled="disabled"><slot /></button>'),
      ElTooltip: make('ElTooltip'),
      ElTable: make('ElTable', '<div><slot :row="{}" :$index="0" /></div>'),
      ElTableColumn: make('ElTableColumn', '<div><slot :row="{}" /></div>'),
      ElProgress: make('ElProgress', '<div />'),
      ElSelect: make('ElSelect', '<div><slot /></div>'),
      ElOption: make('ElOption', '<div><slot /></div>'),
      ElOptionGroup: make('ElOptionGroup', '<div><slot /></div>'),
      ElSegmented: make('ElSegmented', '<div><slot /></div>')
    },
    elMessageSpy: Object.assign(vi.fn(), {
      error: vi.fn(),
      warning: vi.fn(),
      success: vi.fn(),
      info: vi.fn(),
      closeAll: vi.fn()
    })
  }
})

// vitest.config 把 element-plus 深路径统一 alias 回 barrel（'element-plus'），
// 深路径 vi.mock 会全部塌到同一模块 id 互相顶掉，必须按 ServerSsh.spec 的
// 整包口径 mock。
vi.mock('element-plus', () => ({
  ...elementComponents,
  ElMessage: elMessageSpy,
  ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined) }
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useRouter: () => ({ replace: vi.fn() })
  }
})
vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => ({ _id: 'u1', position: ['admin'] })
}))
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key, te: () => false })
  }
})
vi.mock('@/components/server/DbPerfTrendChart.vue', () => ({
  __esModule: true,
  default: { name: 'DbPerfTrendChart', template: '<div />' }
}))

vi.mock('@/api/system/db.ts', () => ({
  getDatabaseStatus: vi.fn(),
  previewRetention: vi.fn(),
  runRetention: vi.fn(),
  getRetentionProgress: vi.fn(),
  runBackup: vi.fn(),
  getBackupProgress: vi.fn(),
  getMetricsRealtime: vi.fn(),
  getMetricsHost: vi.fn(),
  getMetricsHistory: vi.fn(),
  getMetricsTopSql: vi.fn(),
  getMetricsEndpoints: vi.fn(),
  isRetentionJobStart: () => false
}))

const STATUS = {
  data: {
    status: 'ok',
    code: 200,
    data: {
      observed_at: '2026-11-13T10:00:00Z',
      cache: { cached: false, age_seconds: 1, ttl_seconds: 15 },
      warnings: ['配额接近上限'],
      storage: {
        available: true,
        tables: {
          available: true,
          count: 3,
          total_bytes: 1024,
          items: [
            {
              name: 'USERS',
              estimated_rows: 10,
              last_analyzed: '2026-11-01T00:00:00Z',
              table_bytes: 10,
              index_bytes: 2,
              lob_bytes: 0,
              lob_index_bytes: 0,
              total_bytes: 12
            }
          ]
        },
        tablespaces: {
          available: true,
          items: [
            {
              name: 'DATA',
              used_bytes: 50,
              max_bytes: 100,
              used_percent: 50,
              is_application: true
            }
          ]
        }
      },
      load: {
        available: true,
        source: 'V$SYSMETRIC',
        assessment: '',
        metrics: [
          {
            name: 'DB Time',
            value: 12,
            unit: null,
            begin_time: '2026-11-13T09:58:00Z',
            end_time: '2026-11-13T09:59:00Z',
            interval_centiseconds: 100,
            group_id: null
          }
        ],
        connection_pool: {
          available: true,
          scope: 'api',
          size: 10,
          checked_out: 8,
          overflow: 0,
          capacity: 10,
          utilization_percent: 80
        }
      }
    }
  }
}

const REALTIME = {
  data: {
    status: 'ok',
    code: 200,
    data: {
      observed_at: '2026-11-13T10:00:00Z',
      cache: { cached: false, age_seconds: 1, ttl_seconds: 15 },
      storage: STATUS.data.data.storage,
      load: STATUS.data.data.load,
      pool: {
        size: 10,
        checkedout: 8,
        overflow: 0,
        capacity: 10,
        utilization_percent: 80,
        waited: 2
      },
      sessions: {
        total: 12,
        active: 4,
        inactive: 8,
        items: []
      }
    }
  }
}

const HOST = {
  data: {
    status: 'ok',
    code: 200,
    data: {
      observed_at: '2026-11-13T10:00:00Z',
      host: {
        cpu: { user_pct: 20, system_pct: 10, idle_pct: 65, iowait_pct: 5 },
        mem: { total_mb: 4096, used_mb: 2048, available_mb: 2048 },
        load: { '1m': 0.4, '5m': 0.3, '15m': 0.2 },
        disk: { mount: '/', used_pct: 63, free_gb: 120 },
        net: { rx_kbps: 100, tx_kbps: 90 },
        diskio: { read_kbps: 10, write_kbps: 12 },
        proc: { rss_mb: 300, uptime_hours: 72 }
      },
      pool: {
        size: 10,
        checkedout: 3,
        overflow: 0,
        capacity: 10,
        utilization_percent: 30,
        waited: 0
      }
    }
  }
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  )
})

it('renders the database view without runtime errors', async () => {
  const db = await import('@/api/system/db.ts')
  vi.mocked(db.getDatabaseStatus).mockResolvedValue(STATUS as never)
  vi.mocked(db.getRetentionProgress).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { running: false, job_id: null, phase: null, started_at: null, finished_at: null, maintenance: null, progress: null, result: null, error: null, last_run: null } }
  } as never)
  vi.mocked(db.getBackupProgress).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { state: 'idle', tables_done: 0, tables_total: null, rows_loaded: 0, current_table: null, elapsed_seconds: null, message: null } }
  } as never)
  vi.mocked(db.getMetricsRealtime).mockResolvedValue(REALTIME as never)
  vi.mocked(db.getMetricsHost).mockResolvedValue(HOST as never)
  vi.mocked(db.getMetricsHistory).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { series: [], unknown_series: [] } }
  } as never)
  vi.mocked(db.getMetricsTopSql).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { items: [] } }
  } as never)
  vi.mocked(db.getMetricsEndpoints).mockResolvedValue({
    data: { status: 'ok', code: 200, data: { items: [] } }
  } as never)

  const wrapper = mount(ServerDatabase)
  await flushPromises()
  await flushPromises()

  // 状态条存在
  expect(wrapper.find('.phi-srv-strip').exists()).toBe(true)
  // 六边形单元渲染出读数
  expect(wrapper.findAll('.phi-srv-hex').length).toBeGreaterThan(0)
  expect(wrapper.text()).toContain('server.database.unitLabel')

  wrapper.unmount()
})

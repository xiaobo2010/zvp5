import { describe, expect, it } from 'vitest'
import {
  buildPreviewView,
  eventStatusTone,
  isOpsWriteTool,
  markDualAuthCached,
  normalizeEventViews,
  normalizePreview,
  normalizeProposals,
  normalizeStopReason,
  normalizeUsage,
  parseDiffLines,
  parseServerTime,
  previewEntries,
  previewHeadLines,
  previewTitleKey,
  previewTone,
  previewValueText,
  proposalMinutesLeft,
  proposalTone,
  stopReasonHintKey,
  stopReasonKey,
  toolLabelKey
} from './agentSession'

/**
 * 智能体展示模型的纯函数。
 *
 * 后端返回的形状才是契约：这里盯的是「名字翻成哪个文案键」「状态翻成哪种语义色」
 * 「时间怎么解析」，以及脏数据不会把整页打挂。
 */

describe('工具标签', () => {
  it('白名单内的工具翻成自己的 key', () => {
    expect(toolLabelKey('search_activities')).toBe('ai.agent.tools.search_activities')
    expect(toolLabelKey('update_member_duration')).toBe('ai.agent.tools.update_member_duration')
  })

  it('17 个运维工具都有中文标签', () => {
    const ops = [
      'fs_list',
      'fs_stat',
      'fs_read',
      'fs_write',
      'fs_mkdir',
      'fs_rename',
      'fs_delete',
      'fs_chmod',
      'db_schema',
      'db_columns',
      'db_rows',
      'db_query',
      'db_insert',
      'db_update',
      'db_delete',
      'db_ddl',
      'ssh_exec'
    ]
    for (const name of ops) {
      expect(toolLabelKey(name)).toBe(`ai.agent.tools.${name}`)
    }
  })

  it('未知工具与 null 都回落到 unknown', () => {
    expect(toolLabelKey('rm_rf')).toBe('ai.agent.tools.unknown')
    expect(toolLabelKey(null)).toBe('ai.agent.tools.unknown')
    expect(toolLabelKey(undefined)).toBe('ai.agent.tools.unknown')
  })
})

describe('状态语义', () => {
  it('事件状态各有一套语义色', () => {
    expect(eventStatusTone('executed')).toBe('is-success')
    expect(eventStatusTone('proposed')).toBe('is-info')
    expect(eventStatusTone('denied')).toBe('is-warning')
    expect(eventStatusTone('failed')).toBe('is-danger')
  })

  it('提案卡终态与进行中各有一套语义色', () => {
    expect(proposalTone('confirming')).toBe('is-info')
    expect(proposalTone('confirmed')).toBe('is-success')
    expect(proposalTone('expired')).toBe('is-warning')
    expect(proposalTone('error')).toBe('is-danger')
    expect(proposalTone('rejected')).toBe('')
  })
})

describe('提案有效期', () => {
  it('解析服务端的本地时间格式（空格分隔，不是 ISO）', () => {
    const time = parseServerTime('2026-10-25 12:10:00')
    expect(time).toBe(new Date(2026, 9, 25, 12, 10, 0).getTime())
  })

  it('ISO 与垃圾值：认识就给时间，不认识给 null', () => {
    expect(parseServerTime('2026-10-25T12:10:00')).not.toBeNull()
    expect(parseServerTime('十分钟后')).toBeNull()
    expect(parseServerTime('')).toBeNull()
  })

  it('剩余分钟向上取整，过期给 0', () => {
    const now = parseServerTime('2026-10-25 12:00:00')!
    expect(proposalMinutesLeft('2026-10-25 12:10:00', now)).toBe(10)
    expect(proposalMinutesLeft('2026-10-25 12:00:30', now)).toBe(1)
    expect(proposalMinutesLeft('2026-10-25 11:59:00', now)).toBe(0)
    expect(proposalMinutesLeft('坏的', now)).toBeNull()
  })
})

describe('形状收敛', () => {
  it('事件数组：非数组给空，脏行丢弃，合法行带展开态', () => {
    expect(normalizeEventViews(null)).toEqual([])
    expect(normalizeEventViews('nope')).toEqual([])

    const views = normalizeEventViews([
      null,
      'x',
      {
        round_no: 2,
        tool_name: 'list_groups',
        tool_kind: 'read',
        args_summary: null,
        result_summary: 'ok',
        status: 'executed'
      }
    ])

    expect(views).toHaveLength(1)
    expect(views[0]).toEqual({
      round_no: 2,
      tool_name: 'list_groups',
      tool_kind: 'read',
      args_summary: null,
      result_summary: 'ok',
      status: 'executed',
      expanded: false
    })
  })

  it('事件状态/类别不认识时收敛成 failed / null', () => {
    const [view] = normalizeEventViews([{ status: 'weird', tool_kind: 'root', result_summary: 7 }])
    expect(view!.status).toBe('failed')
    expect(view!.tool_kind).toBeNull()
    expect(view!.result_summary).toBe('')
  })

  it('提案数组：缺 oid 的行丢掉（没有句柄就没有按钮）', () => {
    expect(normalizeProposals(undefined)).toEqual([])

    const cards = normalizeProposals([
      { tool_name: 'set_member_status' },
      {
        oid: 'a'.repeat(32),
        tool_name: 'set_member_status',
        args_summary: '{"status":"effective"}',
        preview: null,
        requires_dual_auth: false,
        dual_auth_cached: false,
        expires_at: '2026-10-25 12:10:00'
      }
    ])

    expect(cards).toHaveLength(1)
    expect(cards[0]).toEqual({
      oid: 'a'.repeat(32),
      toolName: 'set_member_status',
      argsSummary: '{"status":"effective"}',
      preview: null,
      requiresDualAuth: false,
      dualAuthCached: false,
      expiresAt: '2026-10-25 12:10:00',
      state: 'pending'
    })
  })

  it('提案数组：带上结构化预览与双人码标记', () => {
    const cards = normalizeProposals([
      {
        oid: 'b'.repeat(32),
        tool_name: 'fs_write',
        args_summary: '{"path":"/etc/x.conf"}',
        preview: {
          kind: 'diff',
          path: '/etc/x.conf',
          diff: '@@ -1 +1,2 @@\n-a\n+b',
          truncated: true
        },
        requires_dual_auth: true,
        dual_auth_cached: false,
        expires_at: '2026-10-25 12:10:00'
      }
    ])

    expect(cards[0]!.requiresDualAuth).toBe(true)
    expect(cards[0]!.preview?.kind).toBe('diff')
  })
})

describe('双人授权缓存', () => {
  it('dual_auth_cached 归一：命中为 true，缺失或非布尔为 false', () => {
    const cards = normalizeProposals([
      {
        oid: 'c'.repeat(32),
        tool_name: 'fs_write',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: true,
        dual_auth_cached: true,
        expires_at: '2026-10-25 12:10:00'
      },
      {
        oid: 'd'.repeat(32),
        tool_name: 'fs_write',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: true,
        expires_at: '2026-10-25 12:10:00'
      },
      {
        oid: 'e'.repeat(32),
        tool_name: 'fs_write',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: true,
        dual_auth_cached: 'yes',
        expires_at: '2026-10-25 12:10:00'
      }
    ])

    expect(cards[0]!.dualAuthCached).toBe(true)
    expect(cards[1]!.dualAuthCached).toBe(false)
    expect(cards[2]!.dualAuthCached).toBe(false)
  })

  it('运维写工具识别：文件 / 数据库写 / 远程命令算，读工具不算', () => {
    for (const name of [
      'fs_write',
      'fs_mkdir',
      'fs_rename',
      'fs_delete',
      'fs_chmod',
      'db_insert',
      'db_update',
      'db_delete',
      'db_ddl',
      'ssh_exec'
    ]) {
      expect(isOpsWriteTool(name)).toBe(true)
    }
    expect(isOpsWriteTool('fs_list')).toBe(false)
    expect(isOpsWriteTool('db_query')).toBe(false)
    expect(isOpsWriteTool('update_member_duration')).toBe(false)
    expect(isOpsWriteTool(null)).toBe(false)
  })

  it('标记免码：只动待确认的运维写卡片，其余原样', () => {
    const cards = normalizeProposals([
      {
        oid: '1'.repeat(32),
        tool_name: 'fs_write',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: true,
        dual_auth_cached: false,
        expires_at: '2026-10-25 12:10:00'
      },
      {
        oid: '2'.repeat(32),
        tool_name: 'db_insert',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: true,
        dual_auth_cached: false,
        expires_at: '2026-10-25 12:10:00'
      },
      {
        oid: '3'.repeat(32),
        tool_name: 'fs_read',
        args_summary: '{}',
        preview: null,
        requires_dual_auth: false,
        dual_auth_cached: false,
        expires_at: '2026-10-25 12:10:00'
      }
    ])
    // 第三张先落终态：不是待确认就不该被标记
    cards[2]!.state = 'confirmed'

    expect(markDualAuthCached(cards)).toBe(2)
    expect(cards[0]!.dualAuthCached).toBe(true)
    expect(cards[1]!.dualAuthCached).toBe(true)
    expect(cards[2]!.dualAuthCached).toBe(false)
  })
})

describe('预览归一', () => {
  it('认识的 kind 保留下来', () => {
    expect(normalizePreview({ kind: 'mkdir', path: '/tmp/x' })).toEqual({
      kind: 'mkdir',
      path: '/tmp/x'
    })
    expect(normalizePreview({ kind: 'rename', from: '/a', to: '/b' })).toEqual({
      kind: 'rename',
      from: '/a',
      to: '/b'
    })
    expect(normalizePreview({ kind: 'chmod', path: '/a', from: '644', to: '755' })).toEqual({
      kind: 'chmod',
      path: '/a',
      from: '644',
      to: '755'
    })
    expect(
      normalizePreview({ kind: 'command', target: 'root@127.0.0.1:22', command: 'uptime' })
    ).toEqual({ kind: 'command', target: 'root@127.0.0.1:22', command: 'uptime' })
  })

  it('不认识的 kind 直接丢弃（后端加了新形态也不能渲染半截）', () => {
    expect(normalizePreview({ kind: 'quantum', path: '/a' })).toBeNull()
    expect(normalizePreview({ path: '/a' })).toBeNull()
    expect(normalizePreview(null)).toBeNull()
    expect(normalizePreview('diff')).toBeNull()
  })

  it('必填字段缺失的行丢弃', () => {
    expect(normalizePreview({ kind: 'diff', path: '/a' })).toBeNull()
    expect(normalizePreview({ kind: 'new_file', content: 'x' })).toBeNull()
    expect(normalizePreview({ kind: 'sql', summary: '建表' })).toBeNull()
  })

  it('行变更与 DDL 的可选字段补默认值', () => {
    expect(normalizePreview({ kind: 'row_update', table: 'USERS' })).toEqual({
      kind: 'row_update',
      table: 'USERS',
      key: {},
      values: {}
    })
    expect(normalizePreview({ kind: 'sql', sql: 'DROP TABLE USERS' })).toEqual({
      kind: 'sql',
      sql: 'DROP TABLE USERS',
      summary: '',
      verb: '',
      target: '',
      destructive: false
    })
  })
})

describe('预览语义', () => {
  it('删除类红、命令与改权限橙、其余蓝', () => {
    expect(previewTone({ kind: 'delete', path: '/a', entries: [] })).toBe('is-danger')
    expect(previewTone({ kind: 'row_delete', table: 'T', key: {} })).toBe('is-danger')
    expect(
      previewTone({
        kind: 'sql',
        sql: 'DROP TABLE T',
        summary: '',
        verb: 'DROP',
        target: 'T',
        destructive: true
      })
    ).toBe('is-danger')
    expect(
      previewTone({
        kind: 'sql',
        sql: 'CREATE TABLE T',
        summary: '',
        verb: 'CREATE',
        target: 'T',
        destructive: false
      })
    ).toBe('is-warning')
    expect(previewTone({ kind: 'command', target: 'h', command: 'ls' })).toBe('is-warning')
    expect(previewTone({ kind: 'diff', path: '/a', diff: '' })).toBe('is-info')
    expect(previewTone(null)).toBe('')
  })

  it('标题键按 kind 给，没有预览时给 none', () => {
    expect(previewTitleKey({ kind: 'diff', path: '/a', diff: '' })).toBe('ai.agent.preview.diff')
    expect(previewTitleKey({ kind: 'row_insert', table: 'T', columns: [], values: {} })).toBe(
      'ai.agent.preview.row_insert'
    )
    expect(previewTitleKey(null)).toBe('ai.agent.preview.none')
  })
})

describe('diff 拆行', () => {
  it('新增/删除/上下文/块头各归各类，文件头跳过', () => {
    const lines = parseDiffLines('--- a/x.conf\n+++ b/x.conf\n@@ -1,2 +1,2 @@\n keep\n-old\n+new')
    expect(lines).toEqual([
      { type: 'hunk', text: '@@ -1,2 +1,2 @@' },
      { type: 'context', text: ' keep' },
      { type: 'remove', text: '-old' },
      { type: 'add', text: '+new' }
    ])
  })

  it('空 diff 给空数组', () => {
    expect(parseDiffLines('')).toEqual([])
  })
})

describe('预览取值', () => {
  it('值转文本：字符串原样，其余 JSON 化，空值给占位符', () => {
    expect(previewValueText('abc')).toBe('abc')
    expect(previewValueText(6)).toBe('6')
    expect(previewValueText(true)).toBe('true')
    expect(previewValueText(null)).toBe('—')
    expect(previewValueText(undefined)).toBe('—')
    expect(previewValueText({ a: 1 })).toBe('{"a":1}')
  })

  it('行变更给键值表：主键条件与待写入值分开', () => {
    const entries = previewEntries({
      kind: 'row_update',
      table: 'USERS',
      key: { OID: 'abc' },
      values: { NAME: '张三' }
    })
    expect(entries).toEqual([
      { label: 'OID', value: 'abc', group: 'key' },
      { label: 'NAME', value: '张三', group: 'value' }
    ])
  })

  it('插入行按列顺序给值，删除行只给主键条件', () => {
    expect(
      previewEntries({ kind: 'row_insert', table: 'T', columns: ['A'], values: { A: 1, B: 2 } })
    ).toEqual([
      { label: 'A', value: '1', group: 'value' },
      { label: 'B', value: '2', group: 'value' }
    ])
    expect(previewEntries({ kind: 'row_delete', table: 'T', key: { A: 1 } })).toEqual([
      { label: 'A', value: '1', group: 'key' }
    ])
    expect(previewEntries({ kind: 'mkdir', path: '/a' })).toEqual([])
    expect(previewEntries(null)).toEqual([])
  })

  it('新建文件只给前若干行正文，并说明被截断', () => {
    const head = previewHeadLines('a\nb\nc', 2)
    expect(head).toEqual({ lines: ['a', 'b'], total: 3, truncated: true })
    expect(previewHeadLines('a', 5).truncated).toBe(false)
  })
})

describe('预览视图模型', () => {
  it('diff 摊平成带行的视图', () => {
    const view = buildPreviewView({ kind: 'diff', path: '/a', diff: '-x\n+y', truncated: true })
    expect(view).toEqual({
      kind: 'diff',
      target: '/a',
      lines: [
        { type: 'remove', text: '-x' },
        { type: 'add', text: '+y' }
      ],
      serverTruncated: true
    })
  })

  it('新建文件只截前 20 行', () => {
    const content = Array.from({ length: 30 }, (_, index) => `line-${index}`).join('\n')
    const view = buildPreviewView({ kind: 'new_file', path: '/a', content })
    expect(view.kind).toBe('new_file')
    if (view.kind === 'new_file') {
      expect(view.lines).toHaveLength(20)
      expect(view.total).toBe(30)
      expect(view.headTruncated).toBe(true)
    }
  })

  it('没有预览时给 none', () => {
    expect(buildPreviewView(null)).toEqual({ kind: 'none' })
  })
})

describe('run 的终止原因与用量', () => {
  it('认识的终止原因保留，不认识的当正常结束', () => {
    expect(normalizeStopReason('timeout')).toBe('timeout')
    expect(normalizeStopReason('token_budget')).toBe('token_budget')
    expect(normalizeStopReason('max_iterations')).toBe('max_iterations')
    expect(normalizeStopReason('cancelled')).toBe('cancelled')
    expect(normalizeStopReason(null)).toBeNull()
    expect(normalizeStopReason('whatever')).toBeNull()
  })

  it('文案键按原因给', () => {
    expect(stopReasonKey('timeout')).toBe('ai.agent.stopReason.timeout')
    expect(stopReasonHintKey('timeout')).toBe('ai.agent.stopReason.timeoutHint')
    expect(stopReasonKey(null)).toBe('ai.agent.stopReason.none')
  })

  it('用量归一：数字留数字，缺失给 null', () => {
    expect(
      normalizeUsage({
        prompt_tokens: 1200,
        completion_tokens: 300,
        total_tokens: 1500,
        cache_read_tokens: 1024,
        cache_write_tokens: 0
      })
    ).toEqual({
      prompt_tokens: 1200,
      completion_tokens: 300,
      total_tokens: 1500,
      cache_read_tokens: 1024,
      cache_write_tokens: 0
    })
    expect(normalizeUsage({ total_tokens: 'x' })).toEqual({
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      cache_read_tokens: null,
      cache_write_tokens: null
    })
    expect(normalizeUsage(null)).toBeNull()
  })
})

import { describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref, watch } from 'vue'
import {
  latestAssistantText,
  mergeToolRows,
  useAgentLive,
  type AgentLiveAssistantFrame,
  type AgentLiveToolFrame
} from './agentLive'

/** 等一帧：适配层用 rAF 合并写入，测试要等真实的一帧过去。 */
async function nextFrame(): Promise<void> {
  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => resolve())
    } else {
      setTimeout(resolve, 16)
    }
  })
}

describe('mergeToolRows', () => {
  it('同一轮同一工具：落库留痕优先，实时帧不重复出现', () => {
    const rows = mergeToolRows(
      [
        {
          round_no: 1,
          tool_name: 'list_activities',
          tool_kind: 'read',
          args_summary: '{"a":1}',
          result_summary: '3 条',
          status: 'executed'
        }
      ],
      [
        {
          type: 'tool',
          seq: 1,
          run_id: 'run-1',
          round_no: 1,
          tool_call_id: 'call-1',
          tool_name: 'list_activities',
          tool_kind: 'read',
          status: 'pending',
          args_summary: '{"a":1}',
          result_summary: null
        }
      ]
    )

    expect(rows).toHaveLength(1)
    expect(rows[0]!.live).toBe(false)
    expect(rows[0]!.status_label_key).toBe('ai.agent.status.executed')
    expect(rows[0]!.status_tone).toBe('is-success')
  })

  it('还没落库的调用按实时帧展示，状态映射到既有词条', () => {
    const rows = mergeToolRows(
      [],
      [
        {
          type: 'tool',
          seq: 2,
          run_id: 'run-1',
          round_no: 2,
          tool_call_id: 'call-2',
          tool_name: 'fs_read',
          tool_kind: 'read',
          status: 'pending',
          args_summary: '/etc/hosts',
          result_summary: null
        }
      ]
    )

    expect(rows).toHaveLength(1)
    expect(rows[0]!.live).toBe(true)
    expect(rows[0]!.status_label_key).toBe('ai.agent.running')
    expect(rows[0]!.args_summary).toBe('/etc/hosts')
  })

  it('写操作保留写标记与既有色调', () => {
    const rows = mergeToolRows(
      [
        {
          round_no: 1,
          tool_name: 'fs_write',
          tool_kind: 'write',
          args_summary: 'a.txt',
          result_summary: 'ok',
          status: 'failed'
        }
      ],
      []
    )

    expect(rows[0]!.tool_kind).toBe('write')
    expect(rows[0]!.status_label_key).toBe('ai.agent.status.failed')
    expect(rows[0]!.status_tone).toBe('is-danger')
  })

  it('实时帧按轮次并入，不会插到落库留痕前面', () => {
    const rows = mergeToolRows(
      [
        {
          round_no: 2,
          tool_name: 'list_users',
          tool_kind: 'read',
          args_summary: null,
          result_summary: 'ok',
          status: 'executed'
        }
      ],
      [
        {
          type: 'tool',
          seq: 1,
          run_id: 'run-1',
          round_no: 1,
          tool_call_id: 'call-1',
          tool_name: 'fs_read',
          tool_kind: 'read',
          status: 'success',
          args_summary: null,
          result_summary: 'ok'
        }
      ]
    )

    expect(rows.map((row) => row.round_no)).toEqual([1, 2])
  })
})

describe('latestAssistantText', () => {
  it('取最新一轮的累计正文', () => {
    const frames: AgentLiveAssistantFrame[] = [
      {
            type: 'assistant',
            seq: 1,
            run_id: 'run-1',
            round_no: 1,
            stream_key: 's1',
            content: '第一段'
          },
      {
        type: 'assistant',
        seq: 2,
        run_id: 'run-1',
        round_no: 2,
        stream_key: 's2',
        content: '第二段累计'
      }
    ]
    expect(latestAssistantText(frames)).toBe('第二段累计')
  })

  it('没有帧时返回空串', () => {
    expect(latestAssistantText([])).toBe('')
  })
})

describe('useAgentLive', () => {
  it('读取状态与累计正文，并按帧合并写入', async () => {
    const assistant = ref<AgentLiveAssistantFrame[]>([])
    const tools = ref<AgentLiveToolFrame[]>([])
    const status = ref<'idle' | 'connecting' | 'live' | 'fallback' | 'closed'>('live')
    const store = {
      streamStatus: () => status.value,
      liveAssistantFor: () => assistant.value,
      liveToolsFor: () => tools.value
    }

    const scope = effectScope()
    const live = scope.run(() => useAgentLive(store, ref('run-1')))!
    let writes = 0
    watch(live.text, () => {
      writes += 1
    })

    assistant.value = [
      {
        type: 'assistant',
        seq: 1,
        run_id: 'run-1',
        round_no: 1,
        stream_key: 's1',
        content: '你好'
      }
    ]
    await nextTick()
    await nextFrame()
    expect(live.text.value).toBe('你好')
    expect(live.status.value).toBe('live')

    // 连续到达的帧合并成一帧写入，不逐 token 触发渲染
    assistant.value = [
      {
        type: 'assistant',
        seq: 2,
        run_id: 'run-1',
        round_no: 1,
        stream_key: 's1',
        content: '你好世界'
      }
    ]
    assistant.value = [
      {
        type: 'assistant',
        seq: 3,
        run_id: 'run-1',
        round_no: 1,
        stream_key: 's1',
        content: '你好世界呀'
      }
    ]
    await nextTick()
    await nextFrame()
    expect(live.text.value).toBe('你好世界呀')
    expect(writes).toBe(2)

    scope.stop()
  })

  it('store 没有实时接口时降级为空，不抛错', async () => {
    const scope = effectScope()
    const live = scope.run(() => useAgentLive({}, ref('')))!
    await nextTick()
    expect(live.status.value).toBe('idle')
    expect(live.text.value).toBe('')
    expect(live.tools.value).toEqual([])
    scope.stop()
  })

  it('没有进行中的 run 时不订阅实时帧', async () => {
    const store = {
      streamStatus: vi.fn(() => 'live' as const),
      liveAssistantFor: vi.fn(() => []),
      liveToolsFor: vi.fn(() => [])
    }
    const scope = effectScope()
    const live = scope.run(() => useAgentLive(store, ref('')))!
    await nextTick()
    expect(store.streamStatus).not.toHaveBeenCalled()
    expect(live.status.value).toBe('idle')
    scope.stop()
  })
})

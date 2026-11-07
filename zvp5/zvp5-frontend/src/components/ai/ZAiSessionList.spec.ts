import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import ZAiSessionList from './ZAiSessionList.vue'
import type { AiConversation } from '@/../types/ai'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const ElButton = defineComponent({
    name: 'ElButton',
    props: { title: { type: String, default: '' }, loading: Boolean },
    emits: ['click'],
    setup(props, { slots, emit }) {
      return () =>
        h(
          'button',
          { class: 'el-button', title: props.title, onClick: () => emit('click') },
          slots.default?.()
        )
    }
  })
  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('i', slots.default?.())
    }
  })
  const ElEmpty = defineComponent({
    name: 'ElEmpty',
    props: { description: { type: String, default: '' } },
    setup(props, { slots }) {
      return () => h('div', { class: 'el-empty' }, [props.description, slots.default?.()])
    }
  })
  const ElSegmented = defineComponent({
    name: 'ElSegmented',
    props: {
      modelValue: { type: String, default: '' },
      options: { type: Array, default: () => [] }
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h(
          'div',
          { class: 'el-segmented' },
          (props.options as Array<{ label: string; value: string }>).map((option) =>
            h(
              'button',
              {
                class: 'el-segmented__item',
                onClick: () => emit('update:modelValue', option.value)
              },
              option.label
            )
          )
        )
    }
  })
  return { ElButton, ElEmpty, ElIcon, ElSegmented }
})

function conversation(overrides: Partial<AiConversation> = {}): AiConversation {
  return {
    oid: 'c1'.repeat(16),
    mode: 'agent',
    title: '查一下图书馆',
    model: 'gpt-4o-mini',
    status: 'active',
    tokens: { prompt: 120, completion: 30, total: 150 },
    context: { tokens: 120, limit: 32768, ratio: 0.0037 },
    compaction: { upto_seq: 0, summarized: false, degraded_at: null },
    message_count: 2,
    created_at: '2026-10-25T00:00:00',
    updated_at: '2026-10-25T01:00:00',
    ...overrides
  }
}

function mountList(
  options: {
    conversations?: AiConversation[]
    archived?: AiConversation[]
    archivedLoading?: boolean
    failed?: boolean
  } = {}
) {
  return mount(ZAiSessionList, {
    props: {
      conversations: options.conversations ?? [conversation()],
      currentOid: '',
      archived: options.archived ?? [],
      archivedLoading: options.archivedLoading ?? false,
      failed: options.failed ?? false
    }
  })
}

async function switchToArchived(wrapper: ReturnType<typeof mountList>) {
  const target = wrapper
    .findAll('.el-segmented__item')
    .find((item) => item.text() === 'ai.chat.sessions.archived')
  expect(target).toBeTruthy()
  await target!.trigger('click')
  await nextTick()
}

describe('ZAiSessionList', () => {
  it('默认展示活动会话，切到已归档时请求归档列表', async () => {
    const wrapper = mountList()

    expect(wrapper.text()).toContain('查一下图书馆')

    await switchToArchived(wrapper)

    expect(wrapper.emitted('loadArchived')).toHaveLength(1)
  })

  it('已归档视图渲染标题与归档标记', async () => {
    const wrapper = mountList({
      archived: [conversation({ title: '旧会话', status: 'archived' })]
    })

    await switchToArchived(wrapper)

    expect(wrapper.text()).toContain('旧会话')
    expect(wrapper.text()).toContain('ai.chat.sessions.archived')
  })

  it('恢复按钮抛出 unarchive 与对应 oid', async () => {
    const archived = conversation({ title: '旧会话', status: 'archived' })
    const wrapper = mountList({ archived: [archived] })

    await switchToArchived(wrapper)
    const restore = wrapper
      .findAll('button')
      .find((item) => item.text() === 'ai.chat.sessions.restore')
    expect(restore).toBeTruthy()

    await restore!.trigger('click')

    expect(wrapper.emitted('unarchive')).toEqual([[archived.oid]])
  })

  it('没有归档会话时显示空态文案', async () => {
    const wrapper = mountList({ archived: [] })

    await switchToArchived(wrapper)

    expect(wrapper.find('.el-empty').text()).toContain('ai.chat.sessions.archivedEmpty')
  })

  it('归档加载中不显示空态', async () => {
    const wrapper = mountList({ archived: [], archivedLoading: true })

    await switchToArchived(wrapper)

    expect(wrapper.find('.el-empty').exists()).toBe(false)
  })
})

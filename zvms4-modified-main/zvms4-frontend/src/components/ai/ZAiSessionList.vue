<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElEmpty, ElIcon } from 'element-plus'
import { Delete, Edit, FolderChecked, Plus } from '@element-plus/icons-vue'
import type { AiConversation } from '@/../types/ai'
import { formatTokens, relativeTimeParts } from './conversationSession'

/**
 * 会话栏（桌面固定在左侧，移动端放进抽屉）。
 *
 * 这一层只负责「长得像列表」与「把意图抛出去」：重命名与删除的确认框
 * 在父级（对话页）里处理，所以这里没有弹层，也不直接调接口。
 */

const props = withDefaults(
  defineProps<{
    conversations: AiConversation[]
    currentOid: string
    loading?: boolean
    failed?: boolean
    hasMore?: boolean
  }>(),
  {
    loading: false,
    failed: false,
    hasMore: false
  }
)

const emit = defineEmits<{
  (event: 'select', oid: string): void
  (event: 'create'): void
  (event: 'rename', oid: string): void
  (event: 'archive', oid: string): void
  (event: 'remove', oid: string): void
  (event: 'loadMore'): void
}>()

const { t } = useI18n()

/** 空态文案：列表空是因为没有会话，还是因为拉失败。 */
const emptyDescription = computed(() => {
  if (props.failed) {
    return t('ai.chat.sessions.loadFailed')
  }
  return t('ai.chat.sessions.empty')
})

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期。 */
function relativeTime(value: string | null | undefined): string {
  const parts = relativeTimeParts(value, Date.now())
  if (parts.kind === 'date') {
    return parts.date || '—'
  }
  if (parts.kind === 'justNow') {
    return t('ai.chat.sessions.justNow')
  }
  return t(`ai.chat.sessions.${parts.kind}Ago`, { count: parts.count })
}
</script>

<template>
  <div class="ai-sessions">
    <div class="ai-sessions__head">
      <span class="ai-sessions__title">{{ t('ai.chat.sessions.title') }}</span>
      <ElButton size="small" text type="primary" @click="emit('create')">
        <ElIcon class="mr-1"><Plus /></ElIcon>
        {{ t('ai.chat.sessions.new') }}
      </ElButton>
    </div>

    <p class="ai-sessions__hint">{{ t('ai.chat.sessions.subtitle') }}</p>

    <div class="ai-sessions__list">
      <p v-if="loading && conversations.length === 0" class="ai-sessions__hint">
        {{ t('ai.common.refresh') }}…
      </p>

      <div
        v-for="item in conversations"
        :key="item.oid"
        class="ai-sessions__item"
        :class="{ 'is-active': item.oid === currentOid }"
        :aria-current="item.oid === currentOid ? 'true' : undefined"
      >
        <button
          type="button"
          class="ai-sessions__main"
          :aria-label="t('ai.chat.sessions.switchTo')"
          @click="emit('select', item.oid)"
        >
          <span class="ai-sessions__name" :title="item.title || t('ai.chat.sessions.untitled')">
            {{ item.title || t('ai.chat.sessions.untitled') }}
          </span>
          <span class="ai-sessions__meta">
            <span class="tabular-nums">{{ relativeTime(item.updated_at) }}</span>
            <span class="tabular-nums">
              {{ t('ai.chat.sessions.tokens', { total: formatTokens(item.tokens.total) }) }}
            </span>
          </span>
        </button>
        <div class="ai-sessions__actions">
          <ElButton
            text
            size="small"
            :title="t('ai.chat.sessions.rename')"
            @click="emit('rename', item.oid)"
          >
            <ElIcon><Edit /></ElIcon>
          </ElButton>
          <ElButton
            text
            size="small"
            :title="t('ai.chat.sessions.archive')"
            @click="emit('archive', item.oid)"
          >
            <ElIcon><FolderChecked /></ElIcon>
          </ElButton>
          <ElButton
            text
            type="danger"
            size="small"
            :title="t('ai.chat.sessions.remove')"
            @click="emit('remove', item.oid)"
          >
            <ElIcon><Delete /></ElIcon>
          </ElButton>
        </div>
      </div>

      <ElButton
        v-if="hasMore"
        text
        size="small"
        class="ai-sessions__more"
        :loading="loading"
        @click="emit('loadMore')"
      >
        {{ t('ai.chat.sessions.loadMore') }}
      </ElButton>

      <ElEmpty
        v-if="!loading && conversations.length === 0"
        :description="emptyDescription"
        :image-size="72"
      >
        <ElButton size="small" type="primary" @click="emit('create')">
          {{ t('ai.chat.sessions.new') }}
        </ElButton>
      </ElEmpty>
    </div>
  </div>
</template>

<style scoped>
.ai-sessions {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  height: 100%;
  min-height: 0;
}

.ai-sessions__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-2);
}

.ai-sessions__title {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.ai-sessions__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  line-height: 1.5;
  color: var(--yg-text-tertiary);
}

.ai-sessions__list {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* 会话项：主按钮占满宽度，操作按钮浮在右侧。 */
.ai-sessions__item {
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-1);
  border: 1px solid transparent;
  border-radius: var(--yg-radius-md);
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-sessions__item:hover {
  background: var(--yg-bg-hover);
}

.ai-sessions__item.is-active {
  border-color: var(--yg-color-primary);
  background: var(--yg-color-primary-soft);
}

.ai-sessions__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: var(--yg-space-2);
  border: 0;
  border-radius: var(--yg-radius-md);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ai-sessions__main:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-sessions__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-primary);
}

.ai-sessions__meta {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-sessions__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 0;
  padding-right: var(--yg-space-1);
}

/* 操作按钮默认淡出，聚焦或悬停时现身：列表本身保持安静。 */
.ai-sessions__actions :deep(.el-button) {
  opacity: 0;
  transition: opacity var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-sessions__item:hover .ai-sessions__actions :deep(.el-button),
.ai-sessions__item:focus-within .ai-sessions__actions :deep(.el-button) {
  opacity: 1;
}

.ai-sessions__more {
  align-self: flex-start;
}
</style>

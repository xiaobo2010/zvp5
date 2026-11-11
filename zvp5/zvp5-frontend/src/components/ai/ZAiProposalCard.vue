<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { CopyDocument } from '@element-plus/icons-vue'
import {
  buildPreviewView,
  previewTitleKey,
  previewTone,
  proposalMinutesLeft,
  proposalTone,
  toolLabelKey,
  type AgentProposalCard
} from './agentSession'

/**
 * 提案卡（只读渲染 + 两个动作）。
 *
 * 状态由父级持有（`AgentProposalCard` 是父级数组里的对象），本组件只负责
 * 「怎么把提案画出来」：结构化预览（diff / 命令 / 行变更…）、有效期、操作按钮、
 * 执行结果与错误说明。确认与驳回通过事件抛给父级——动态码弹窗、请求与状态机
 * 都在父级统一处理，列表页与待确认入口才能共用同一套逻辑。
 */
const props = defineProps<{
  card: AgentProposalCard
  /** 当前时间（毫秒）：父级按 30 秒推进，用于算有效期。 */
  now: number
}>()

const emit = defineEmits<{
  (event: 'confirm'): void
  (event: 'reject'): void
}>()

const { t } = useI18n()

/** 处理中（尚未落终态）才给操作按钮。 */
const actionable = computed(
  () =>
    props.card.state === 'pending' ||
    props.card.state === 'confirming' ||
    props.card.state === 'rejecting'
)

/** 结构化预览的视图模型（没有预览时 kind='none'，整块不渲染）。 */
const view = computed(() => buildPreviewView(props.card.preview))

/** 有效期文案；解析不了服务端时间就回落到固定文案。 */
const validity = computed(() => {
  if (props.card.state === 'expired') {
    return t('ai.agent.proposal.expired')
  }
  const minutes = proposalMinutesLeft(props.card.expiresAt, props.now)
  if (minutes === null) {
    return t('ai.agent.proposal.validUnknown')
  }
  if (minutes <= 0) {
    return t('ai.agent.proposal.expired')
  }
  return t('ai.agent.proposal.validFor', { minutes })
})

async function copyValue(value: string | null | undefined) {
  if (!value) {
    return
  }
  try {
    await navigator.clipboard.writeText(value)
    t('ai.common.copied')
  } catch {
    /* 剪贴板不可用时不做提示：请求编号本身已经显示在卡片上 */
  }
}
</script>

<template>
  <section class="ai-proposal" :class="`is-${card.state}`">
    <div class="ai-proposal__head">
      <span class="ai-proposal__dot" aria-hidden="true" />
      <span class="ai-proposal__tool">{{ t(toolLabelKey(card.toolName)) }}</span>
      <!-- 双人码：写操作要另一位管理员放行。命中 30 分钟窗口时不再要码，标签也换成中性提示 -->
      <span
        v-if="card.requiresDualAuth && !card.dualAuthCached"
        class="ai-proposal__badge is-warning"
      >
        {{ t('ai.agent.dualAuth.badge') }}
      </span>
      <span v-else-if="card.dualAuthCached" class="ai-proposal__cached">
        {{ t('ai.agent.dualAuth.cached') }}
      </span>
      <span class="ai-proposal__badge" :class="proposalTone(card.state)">
        {{ t(`ai.agent.proposal.${card.state}`) }}
      </span>
    </div>

    <!-- 结构化预览：确认前必须看清「到底要改什么」 -->
    <div v-if="view.kind !== 'none'" class="ai-preview" :class="previewTone(card.preview)">
      <div class="ai-preview__head">
        <span class="ai-proposal__badge" :class="previewTone(card.preview)">
          {{ t(previewTitleKey(card.preview)) }}
        </span>
        <span v-if="view.target" class="ai-preview__target">{{ view.target }}</span>
      </div>

      <!-- 文件差异：逐行着色，+/- 一眼可辨 -->
      <div v-if="view.kind === 'diff'" class="ai-preview__diff">
        <span
          v-for="(line, index) in view.lines"
          :key="index"
          class="ai-preview__line"
          :class="`is-${line.type}`"
          >{{ line.text }}</span
        >
      </div>

      <!-- 新建文件：只给前若干行正文 -->
      <div v-else-if="view.kind === 'new_file'" class="ai-preview__code">
        <span
          v-for="(line, index) in view.lines"
          :key="index"
          class="ai-preview__line is-context"
          >{{ line }}</span
        >
        <p v-if="view.headTruncated" class="ai-preview__hint">
          {{
            t('ai.agent.previewFields.contentHead', {
              shown: view.lines.length,
              total: view.total
            })
          }}
        </p>
      </div>

      <!-- 远程命令：等宽 + 可滚动，不撑破页面 -->
      <div v-else-if="view.kind === 'command'" class="ai-preview__code">
        <p class="ai-preview__meta">{{ t('ai.agent.previewFields.target') }}：{{ view.target }}</p>
        <pre class="ai-preview__mono">{{ view.command }}</pre>
      </div>

      <!-- DDL：等宽 + 可滚动，破坏性语句额外标红 -->
      <div v-else-if="view.kind === 'sql'" class="ai-preview__code">
        <p class="ai-preview__meta">
          <span v-if="view.verb">{{ t('ai.agent.previewFields.verb') }}：{{ view.verb }}</span>
          <span v-if="view.target"> · {{ view.target }}</span>
          <span v-if="view.destructive" class="ai-preview__danger">
            · {{ t('ai.agent.previewFields.destructive') }}
          </span>
        </p>
        <pre class="ai-preview__mono">{{ view.sql }}</pre>
        <p v-if="view.summary" class="ai-preview__hint">{{ view.summary }}</p>
      </div>

      <!-- 重命名 / 改权限：两行键值 -->
      <div v-else-if="view.kind === 'rename'" class="ai-preview__pairs">
        <div class="ai-preview__pair">
          <span class="ai-preview__label">{{ t('ai.agent.previewFields.from') }}</span>
          <span class="ai-preview__value">{{ view.from }}</span>
        </div>
        <div class="ai-preview__pair">
          <span class="ai-preview__label">{{ t('ai.agent.previewFields.to') }}</span>
          <span class="ai-preview__value">{{ view.to }}</span>
        </div>
      </div>
      <div v-else-if="view.kind === 'chmod'" class="ai-preview__pairs">
        <div class="ai-preview__pair">
          <span class="ai-preview__label">{{ t('ai.agent.previewFields.path') }}</span>
          <span class="ai-preview__value">{{ view.target }}</span>
        </div>
        <div class="ai-preview__pair">
          <span class="ai-preview__label">{{ t('ai.agent.previewFields.mode') }}</span>
          <span class="ai-preview__value">
            <code class="ai-preview__mono-inline">{{ view.from }}</code>
            →
            <code class="ai-preview__mono-inline">{{ view.to }}</code>
          </span>
        </div>
      </div>

      <!-- 删除：目标 + 将被删掉的条目 -->
      <div v-else-if="view.kind === 'delete'" class="ai-preview__code">
        <p class="ai-preview__meta">{{ t('ai.agent.previewFields.path') }}：{{ view.target }}</p>
        <p class="ai-preview__hint">{{ t('ai.agent.previewFields.entries') }}</p>
        <ul class="ai-preview__entries">
          <li v-for="(entry, index) in view.entries" :key="index">{{ entry }}</li>
        </ul>
        <p v-if="view.entriesTruncated" class="ai-preview__hint">
          {{ t('ai.agent.previewFields.entriesTruncated') }}
        </p>
      </div>

      <!-- 行变更：键值表 -->
      <div v-else-if="view.kind === 'row'" class="ai-preview__code">
        <p class="ai-preview__meta">{{ t('ai.agent.previewFields.table') }}：{{ view.target }}</p>
        <div class="yg-descriptions yg-descriptions--bordered ai-preview__descriptions">
          <div v-for="(entry, index) in view.entries" :key="index" class="yg-descriptions__item">
            <span class="yg-descriptions__label">{{ entry.label }}</span>
            <span class="yg-descriptions__value ai-preview__mono-inline">{{ entry.value }}</span>
          </div>
        </div>
      </div>

      <!-- 新建目录 -->
      <div v-else-if="view.kind === 'mkdir'" class="ai-preview__code">
        <p class="ai-preview__meta">{{ t('ai.agent.previewFields.path') }}：{{ view.target }}</p>
      </div>

      <!-- 活动域写工具的回落预览 -->
      <div v-else-if="view.kind === 'args'" class="ai-preview__code">
        <pre class="ai-preview__mono">{{ view.text }}</pre>
      </div>

      <p v-if="view.kind === 'diff' && view.serverTruncated" class="ai-preview__hint">
        {{ t('ai.agent.previewFields.truncated') }}
      </p>
      <p v-if="view.kind === 'new_file' && view.serverTruncated" class="ai-preview__hint">
        {{ t('ai.agent.previewFields.truncated') }}
      </p>
    </div>

    <!-- 参数摘要：预览之外的原始参数，随时可查 -->
    <details v-if="card.argsSummary" class="ai-proposal__raw">
      <summary class="ai-proposal__hint">{{ t('ai.agent.preposal.rawArgs') }}</summary>
      <code class="ai-proposal__args">{{ card.argsSummary }}</code>
    </details>

    <p v-if="actionable" class="ai-proposal__hint">{{ validity }}</p>

    <div v-if="actionable" class="ai-proposal__actions">
      <ElButton
        class="ai-proposal__action"
        type="primary"
        size="small"
        :loading="card.state === 'confirming'"
        :disabled="card.state !== 'pending'"
        @click="emit('confirm')"
      >
        {{ t('ai.agent.proposal.confirm') }}
      </ElButton>
      <ElButton
        class="ai-proposal__action"
        size="small"
        :loading="card.state === 'rejecting'"
        :disabled="card.state !== 'pending'"
        @click="emit('reject')"
      >
        {{ t('ai.agent.proposal.reject') }}
      </ElButton>
    </div>

    <div v-if="card.resultSummary" class="ai-proposal__result">
      <span class="ai-proposal__hint">{{ t('ai.agent.proposal.result') }}</span>
      <code class="ai-proposal__result-text">{{ card.resultSummary }}</code>
    </div>

    <div v-if="card.errorTitle" class="ai-proposal__error">
      <div class="ai-proposal__error-head">
        <span class="ai-proposal__error-title">{{ card.errorTitle }}</span>
        <ElTag v-if="card.errorCode" type="danger" size="small">
          {{ card.errorCode }}
        </ElTag>
      </div>
      <p class="ai-proposal__hint">{{ card.errorHint }}</p>
      <div v-if="card.requestId" class="ai-proposal__error-foot">
        <span class="ai-proposal__hint">{{ t('ai.chat.requestId') }}</span>
        <code class="ai-proposal__mono-inline tabular-nums">{{ card.requestId }}</code>
        <ElButton text size="small" @click="copyValue(card.requestId)">
          <ElIcon class="mr-1"><CopyDocument /></ElIcon>
          {{ t('ai.chat.copyRequestId') }}
        </ElButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* ---------------------------------------------------------------------------
   提案卡：写操作的确认界面。
   颜色/间距/圆角全部走 --yg-* 令牌；diff 的红绿是「增删」语义，不是装饰色。
   --------------------------------------------------------------------------- */

.ai-proposal {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin: var(--yg-space-2) 0;
  padding: var(--yg-space-3);
  border: 1px solid var(--yg-border-default);
  border-left: 2px solid var(--yg-color-warning);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  transition: border-color var(--yg-dur-base) var(--yg-ease-out);
}

.ai-proposal.is-confirmed {
  border-left-color: var(--yg-color-success);
}

.ai-proposal.is-error {
  border-left-color: var(--yg-color-danger);
}

.ai-proposal.is-rejected,
.ai-proposal.is-expired {
  border-left-color: var(--yg-border-strong);
}

.ai-proposal__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-proposal__dot {
  flex: none;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-color-warning);
}

.ai-proposal__tool {
  flex: none;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

.ai-proposal__badge {
  flex: none;
  padding: 0 var(--yg-space-1);
  border-radius: var(--yg-radius-xs);
  background: var(--yg-bg-surface);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  line-height: 1.7;
}

.ai-proposal__badge.is-success {
  background: var(--yg-color-success-soft);
  color: var(--yg-text-success);
}

.ai-proposal__badge.is-warning {
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.ai-proposal__badge.is-danger {
  background: var(--yg-color-danger-soft);
  color: var(--yg-text-danger);
}

.ai-proposal__badge.is-info {
  background: var(--yg-color-info-soft);
  color: var(--yg-text-link);
}

/* 授权缓存命中：中性提示，不用警告色（它不是风险，是省事） */
.ai-proposal__cached {
  flex: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

/* 预览块：比卡片再深一层的凹陷区，视觉上属于卡片内部 */
.ai-preview {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-2);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface);
}

.ai-preview__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-preview__target {
  min-width: 0;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  word-break: break-all;
}

.ai-preview__meta {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
  word-break: break-word;
}

.ai-preview__danger {
  color: var(--yg-text-danger);
  font-weight: var(--yg-font-weight-medium);
}

.ai-preview__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-preview__code {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-width: 0;
}

/* diff：逐行着色。max-height + overflow 让长差异在卡片内滚动，不撑破页面 */
.ai-preview__diff {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-preview__line {
  display: block;
  padding: 0 var(--yg-space-2);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  line-height: 1.7;
  white-space: pre;
  color: var(--yg-text-secondary);
}

.ai-preview__line.is-add {
  background: var(--yg-color-success-soft);
  color: var(--yg-text-success);
}

.ai-preview__line.is-remove {
  background: var(--yg-color-danger-soft);
  color: var(--yg-text-danger);
}

.ai-preview__line.is-hunk {
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-link);
  font-weight: var(--yg-font-weight-medium);
}

.ai-preview__mono {
  margin: 0;
  padding: var(--yg-space-2);
  max-height: 240px;
  overflow: auto;
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--yg-text-primary);
}

.ai-preview__mono-inline {
  font-family: var(--yg-font-mono);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

.ai-preview__pairs {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

.ai-preview__pair {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-preview__label {
  flex: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-preview__value {
  min-width: 0;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-primary);
  word-break: break-all;
}

.ai-preview__descriptions {
  font-size: var(--yg-font-size-xs);
}

.ai-preview__entries {
  margin: 0;
  padding-left: var(--yg-space-4);
  max-height: 200px;
  overflow: auto;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
}

.ai-proposal__raw {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  min-width: 0;
}

.ai-proposal__raw > summary {
  cursor: pointer;
  list-style: none;
}

.ai-proposal__raw > summary::-webkit-details-marker {
  display: none;
}

.ai-proposal__args {
  display: block;
  max-height: 160px;
  overflow: auto;
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--yg-text-tertiary);
}

.ai-proposal__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-proposal__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

/* 写操作的主操作与驳回都要点得准：触控目标 44px */
.ai-proposal__action {
  min-height: 44px;
  min-width: 44px;
}

.ai-proposal__result {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface);
}

.ai-proposal__result-text {
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--yg-text-secondary);
}

.ai-proposal__error {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  padding: var(--yg-space-2);
  border-left: 2px solid var(--yg-color-danger);
  background: var(--yg-bg-surface);
}

.ai-proposal__error-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-proposal__error-title {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-danger);
}

.ai-proposal__error-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
}

/* 窄屏：预览区继续在卡内滚动，不横向撑破整页 */
@media (max-width: 640px) {
  .ai-preview__diff,
  .ai-preview__mono,
  .ai-preview__entries {
    max-height: 200px;
  }

  .ai-preview__line {
    white-space: pre-wrap;
    word-break: break-all;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-proposal {
    transition: none;
  }
}
</style>

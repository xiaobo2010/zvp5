<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AiProposal } from '@/types/domain'
import { formatDateTime } from '@/shared/lib/format'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'

const props = defineProps<{ proposal: AiProposal; busy?: boolean }>()

const emit = defineEmits<{
  confirm: [proposal: AiProposal]
  reject: [proposal: AiProposal]
}>()

const { t } = useI18n()

const summary = computed(
  () => props.proposal.summary || props.proposal.args_summary || props.proposal.tool_name || ''
)
</script>

<template>
  <article class="proposal">
    <header class="head">
      <div class="tags">
        <ZBadge tone="accent">{{ t('ai.proposalBadge') }}</ZBadge>
        <code v-if="proposal.tool_name" class="tool">{{ proposal.tool_name }}</code>
        <ZBadge v-if="proposal.requires_dual_auth && !proposal.dual_auth_cached" tone="warning">
          {{ t('ai.proposalDual') }}
        </ZBadge>
        <ZBadge v-else-if="proposal.requires_dual_auth" tone="info">
          {{ t('ai.proposalCached') }}
        </ZBadge>
      </div>
      <span v-if="proposal.expires_at" class="expires">
        {{ t('ai.proposalExpires', { time: formatDateTime(proposal.expires_at) }) }}
      </span>
    </header>

    <p class="summary">{{ summary }}</p>

    <details v-if="proposal.args_summary && proposal.args_summary !== summary" class="args">
      <summary>{{ t('ai.proposalArgs') }}</summary>
      <pre>{{ proposal.args_summary }}</pre>
    </details>

    <footer class="foot">
      <ZButton size="sm" variant="ghost" :disabled="busy" @click="emit('reject', proposal)">
        {{ t('ai.proposalReject') }}
      </ZButton>
      <ZButton size="sm" variant="primary" :disabled="busy" @click="emit('confirm', proposal)">
        {{ t('ai.proposalConfirm') }}
      </ZButton>
    </footer>
  </article>
</template>

<style scoped>
.proposal {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--line));
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--accent-soft) 34%, var(--surface));
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}

.tool {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--ink-muted);
}

.expires {
  flex: none;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.summary {
  font-size: var(--text-sm);
  color: var(--ink-soft);
  line-height: 1.6;
  word-break: break-word;
}

.args summary {
  cursor: pointer;
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.args pre {
  margin: 0.5rem 0 0;
  max-height: 220px;
  overflow: auto;
  padding: 0.7rem 0.8rem;
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>

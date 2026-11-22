<script setup lang="ts">
import { getStatusClass, getStatusLabel, type Activity } from './volunteerStack'

const props = defineProps<{
  activities: Activity[]
  listLoading?: boolean
}>()
const expandedActivityId = defineModel<string | null>({ required: true })
const emit = defineEmits<{
  'card-escape': [event: KeyboardEvent, id: string]
}>()

defineSlots<{ status?: (props: { activity: Activity }) => unknown }>()

function toggleCard(id: string) {
  if (props.listLoading) return
  expandedActivityId.value = expandedActivityId.value === id ? null : id
}

function handleCardKeydown(event: KeyboardEvent, id: string) {
  if (event.key !== 'Escape' || expandedActivityId.value !== id) return
  emit('card-escape', event, id)
  if (event.defaultPrevented) return
  event.preventDefault()
  event.stopPropagation()
  expandedActivityId.value = null
  if (event.currentTarget instanceof HTMLElement) {
    event.currentTarget.querySelector('summary')?.focus()
  }
}
</script>

<template>
  <div
    class="activity-stack"
    :class="{ 'is-refreshing': listLoading }"
    :aria-busy="listLoading"
  >
    <details
      v-for="activity in activities"
      :key="activity.rowKey"
      class="activity-card glass-card"
      :open="expandedActivityId === activity.rowKey"
      @keydown="handleCardKeydown($event, activity.rowKey)"
    >
      <summary class="activity-card__summary" :aria-disabled="listLoading || undefined" @click.prevent="toggleCard(activity.rowKey)">
        <span class="activity-card__title">{{ activity.name }}</span>
        <span class="activity-card__meta">
          <span class="activity-card__date">{{ activity.detailDate }}</span>
          <span class="activity-card__type" :class="activity.typeClass">{{ activity.type }}</span>
          <span v-if="activity.hasDuration" class="activity-card__duration">{{ activity.duration }} h</span>
        </span>
      </summary>

      <div v-if="expandedActivityId === activity.rowKey" class="activity-card__details">
        <dl class="detail-info">
          <div class="detail-info-row">
            <dt>活动时间</dt>
            <dd>{{ activity.detailDate }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>地点</dt>
            <dd>{{ activity.place || '—' }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>类型</dt>
            <dd><span class="detail-type" :class="activity.typeClass">{{ activity.type }}</span></dd>
          </div>
          <div v-if="activity.creator" class="detail-info-row">
            <dt>创建者</dt>
            <dd>{{ activity.creator }}</dd>
          </div>
          <div class="detail-info-row">
            <dt>审核状态</dt>
            <dd class="detail-status-cell">
              <slot name="status" :activity="activity">
                <span class="detail-status" :class="activity.statusClass">{{ activity.statusLabel }}</span>
              </slot>
            </dd>
          </div>
          <div v-if="activity.memberStatus" class="detail-info-row">
            <dt>个人记录状态</dt>
            <dd><span class="detail-status" :class="getStatusClass(activity.memberStatus)">{{ getStatusLabel(activity.memberStatus) }}</span></dd>
          </div>
          <div class="detail-info-row">
            <dt>时长</dt>
            <dd>{{ activity.hasDuration ? `${activity.duration}h` : '—' }}</dd>
          </div>
        </dl>

        <div class="detail-actions">
          <RouterLink
            class="liquid-btn liquid-btn--secondary liquid-btn--sm"
            :to="{ name: 'volunteer-detail', params: { id: activity.id } }"
            :aria-label="`查看${activity.name}的详情`"
          >
            查看详情
          </RouterLink>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.glass-card {
  position: relative;
  background: var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
}

.glass-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl);
  pointer-events: none;
  z-index: 0;
}

.glass-card > * {
  position: relative;
  z-index: 1;
}

.activity-stack {
  --card-overlap: clamp(32px, 6vw, 52px);
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  width: min(100%, 620px);
  margin-inline: auto;
  isolation: isolate;
}

.activity-stack.is-refreshing {
  opacity: 0.6;
  pointer-events: none;
}

.activity-card {
  position: relative;
  padding: 14px clamp(14px, 4vw, 28px) calc(var(--card-overlap) + 14px);
  background: linear-gradient(125deg, #fbfcff 24%, var(--palette-edge, #dce9f5));
  border-color: color-mix(in srgb, var(--accent-ink) 22%, #fff);
  box-shadow: var(--glass-rim), 0 3px 12px #102c471f;
  transition:
    transform 280ms var(--ease-settle),
    border-color var(--duration-fast) var(--ease-settle),
    box-shadow 280ms var(--ease-settle);
}

.activity-card + .activity-card { margin-block-start: calc(-1 * var(--card-overlap)); }
.activity-card:last-child { padding-block-end: clamp(32px, 8vw, 72px); }
.activity-card:nth-child(even) { background: linear-gradient(125deg, #f9fbff 24%, var(--palette-main, #d9eafd)); }

.activity-card[open] {
  padding-block-end: var(--space-5);
  border-color: var(--accent-ink);
  box-shadow: var(--glass-rim), 0 8px 28px rgba(16, 44, 71, 0.15);
}

.activity-card[open] + .activity-card { margin-block-start: var(--space-3); }

.activity-card__summary {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: pointer;
  list-style: none;
  -webkit-user-select: none;
  user-select: none;
  padding-inline-end: 20px;
}

.activity-card__summary::after {
  content: '';
  position: absolute;
  inset-block-start: 7px;
  inset-inline-end: 3px;
  width: 7px;
  height: 7px;
  border-inline-end: 1.5px solid var(--accent-ink);
  border-block-end: 1.5px solid var(--accent-ink);
  rotate: 45deg;
  transition: rotate var(--duration-fast) var(--ease-settle);
}

.activity-card[open] > .activity-card__summary::after { rotate: 225deg; }

.activity-card__summary::-webkit-details-marker {
  display: none;
}

@media (hover: hover) and (pointer: fine) {
  .activity-card:not([open]):hover {
    border-color: var(--accent-ink);
    box-shadow: var(--glass-rim), 0 6px 20px rgba(16, 44, 71, 0.12);
  }
}

@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .activity-card:not([open]):hover { transform: translateY(-2px) scale(1.02); }
}

.activity-card__summary:focus-visible {
  outline: 2px solid var(--accent-ink);
  outline-offset: -3px;
  border-radius: var(--radius-md);
}

.activity-card__title {
  margin: 0;
  font-size: clamp(1.0625rem, 1rem + 0.3vw, 1.1875rem);
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.3;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

.activity-card[open] .activity-card__title {
  display: block;
}

.activity-card__date {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.activity-card__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  font-size: var(--font-size-callout);
}

.activity-card__type {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 0.8125rem;
  font-weight: 500;
}

.activity-card__duration { color: var(--accent-ink); font-weight: 600; }

.activity-card__details {
  margin-block-start: var(--space-4);
  padding-block-start: var(--space-4);
  border-block-start: 1px solid var(--color-border-hairline);
  animation: card-expand 320ms var(--ease-settle) backwards;
}

@keyframes card-expand {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
}

.detail-info {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.detail-info-row {
  display: grid;
  grid-template-columns: min(5rem, 30%) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: baseline;
}

.detail-info-row dt {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-tertiary);
}

.detail-info-row dd {
  margin: 0;
  font-size: 0.9375rem;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-type {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  background: rgba(56, 189, 248, 0.1);
  border-radius: 999px;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--accent-ink);
}

.activity-type--on-campus { background: rgba(56, 189, 248, 0.1); color: var(--accent-ink); }
.activity-type--off-campus { background: rgba(110, 231, 183, 0.1); color: rgb(5, 150, 105); }
.activity-type--social-practice { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }
.activity-type--hybrid { background: rgba(167, 139, 250, 0.1); color: rgb(109, 40, 217); }

.detail-status {
  display: inline-block;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.status-draft { background: rgba(148, 163, 184, 0.1); color: rgb(71, 85, 105); }
.status-pending { background: rgba(251, 191, 36, 0.1); color: rgb(180, 83, 9); }
.status-refused { background: rgba(239, 68, 68, 0.1); color: rgb(185, 28, 28); }
.status-effective { background: rgba(34, 197, 94, 0.1); color: rgb(21, 128, 61); }

.detail-status-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
  padding-block-start: var(--space-4);
  border-block-start: 1px solid var(--color-border-hairline);
}

.liquid-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  font-size: var(--font-size-callout);
  font-weight: 600;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    transform 280ms cubic-bezier(0.34, 1.08, 0.64, 1),
    box-shadow 280ms cubic-bezier(0.4, 0, 0.2, 1);
  isolation: isolate;
  overflow: hidden;
  border: 1px solid;
  min-height: 44px;
}

.liquid-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.75) 0%, rgba(255, 255, 255, 0) 100%);
  border-radius: var(--radius-pill) var(--radius-pill) 0 0;
  pointer-events: none;
  opacity: 0.5;
}

.liquid-btn:active {
  transform: scale(0.96);
}

.liquid-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.liquid-btn:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.liquid-btn--secondary {
  background: var(--glass-floating-bg, linear-gradient(135deg, rgba(255, 255, 255, 0.76) 0%, rgba(232, 242, 252, 0.56) 100%));
  -webkit-backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  backdrop-filter: var(--glass-blur, blur(12px) saturate(180%));
  border-color: var(--glass-border, rgba(255, 255, 255, 0.88));
  color: var(--color-text-primary);
  box-shadow:
    0 3px 10px rgba(18, 48, 82, 0.08),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.96));
}

.liquid-btn--secondary:hover:not(:disabled) {
  box-shadow:
    0 4px 12px rgba(18, 48, 82, 0.1),
    var(--glass-rim, inset 0 1px 0 rgba(255, 255, 255, 0.92));
  transform: scale(1.02);
}

.liquid-btn--sm {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  min-height: 36px;
}

@media (max-width: 374px) {
  .activity-card__title {
    font-size: 1rem;
  }

  .activity-card__meta {
    font-size: 0.8125rem;
  }

  .detail-info-row {
    grid-template-columns: min(4.5rem, 30%) minmax(0, 1fr);
    gap: var(--space-2);
  }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-btn:active { transform: none; }

  .activity-card,
  .activity-card__summary::after,
  .activity-card__details {
    transition: none;
    animation: none;
  }
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ZGlassSurface from '@/shared/ui/ZGlassSurface.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import PaginationControls from './PaginationControls.vue'
import PageSizeSelector from './PageSizeSelector.vue'
import PaginationLayoutSelector from './PaginationLayoutSelector.vue'
import type { PageColumns } from '@/shared/composables/useAdaptivePagination'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    auto?: boolean
    columns?: PageColumns
    allowAuto?: boolean
    allowLayout?: boolean
    pageSizeOptions?: number[]
    disabled?: boolean
    /** 完整列表页路由；提供时显示独立的「查看全部」入口 */
    allTo?: string
  }>(),
  {
    auto: false,
    columns: 1,
    allowAuto: true,
    allowLayout: false,
    pageSizeOptions: () => [6, 10, 20, 40, 60],
    disabled: false
  }
)

const emit = defineEmits<{
  'update:page': [page: number]
  'update:pageSize': [size: number]
  'update:auto': [value: boolean]
  'update:columns': [value: PageColumns]
}>()

const { t } = useI18n()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))
const start = computed(() => (props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1))
const end = computed(() => Math.min(props.total, props.page * props.pageSize))
</script>

<template>
  <div v-if="total > 0" class="adapt glass-host">
    <ZGlassSurface material="subtle" depth="flat" />

    <div class="bar">
      <div class="meta">
        <span class="range num">{{ start }}–{{ end }}</span>
        <span class="total">/ {{ total }} {{ t('pagination.rows') }}</span>
        <span class="page-of num">{{ page }} / {{ totalPages }}</span>
      </div>

      <PaginationControls
        :page="page"
        :total-pages="totalPages"
        :disabled="disabled"
        @update:page="emit('update:page', $event)"
      />

      <div class="tools">
        <PageSizeSelector
          :model-value="pageSize"
          :options="pageSizeOptions"
          :auto="auto"
          :disabled="disabled"
          @update:model-value="emit('update:pageSize', $event)"
          @update:auto="emit('update:auto', $event)"
        />

        <PaginationLayoutSelector
          v-if="allowLayout"
          :model-value="columns"
          @update:model-value="emit('update:columns', $event)"
        />

        <RouterLink v-if="allTo" :to="allTo" class="all">
          {{ t('pagination.all') }}
          <ZIcon name="arrow-right" :size="15" />
        </RouterLink>
      </div>
    </div>
  </div>
</template>

<style scoped>
.adapt {
  position: relative;
  border: 1px solid color-mix(in srgb, var(--glass-border) 80%, transparent);
  border-radius: var(--radius-xl, 22px);
  box-shadow: var(--shadow-sm), inset 0 1px 0 var(--glass-hi);
}

.bar {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 0.55rem 0.85rem;
}

.meta {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  min-width: 8.5rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  white-space: nowrap;
}

.range {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-soft);
}

.page-of {
  margin-left: var(--space-2);
  padding-left: var(--space-2);
  border-left: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
  color: var(--ink-muted);
}

.tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.all {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  height: 30px;
  padding: 0 0.7rem;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--glass-border));
  color: var(--accent-ink);
  font-size: var(--text-2xs);
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.all:hover {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

.all:active {
  transform: scale(0.97);
}

@media (max-width: 900px) {
  .bar {
    flex-wrap: wrap;
    justify-content: center;
    row-gap: var(--space-2);
  }

  .meta {
    min-width: 0;
  }

  .tools {
    margin-left: auto;
  }
}

@media (max-width: 640px) {
  .bar {
    justify-content: space-between;
  }

  .meta {
    order: 1;
  }

  :deep(.pager) {
    order: 3;
    width: 100%;
    justify-content: space-between;
  }

  .tools {
    order: 2;
    margin-left: 0;
    gap: var(--space-1, 4px);
  }
}
</style>

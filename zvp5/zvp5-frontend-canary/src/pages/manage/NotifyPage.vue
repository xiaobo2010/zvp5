<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { verifyPassword } from '@/shared/api/auth'
import {
  createNotification,
  fetchAdminNotifications,
  updateNotification
} from '@/shared/api/manage'
import type { AdminNotification } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import MarkdownEditor from '@/shared/ui/markdown/MarkdownEditor.vue'
import ManageTabs from './ManageTabs.vue'

const { t } = useI18n()
const session = useSessionStore()
const ui = useUiStore()

const items = ref<AdminNotification[]>([])
const loading = ref(true)
const error = ref('')

const formOpen = ref(false)
const editing = ref<AdminNotification | null>(null)
const draftTitle = ref('')
const draftContent = ref('')
const draftActive = ref('active')
const password = ref('')
const formError = ref('')
const saving = ref(false)

const stateOptions = computed(() => [
  { value: 'active', label: t('manage.notify.activeState') },
  { value: 'inactive', label: t('manage.notify.inactive') }
])

const isEditing = computed(() => editing.value !== null)

/** 列表里只做纯文本预览：去掉 Markdown 标记，避免满屏 ### 与 **。 */
function preview(text: string): string {
  return text
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/`{1,3}/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchAdminNotifications()
    items.value = result.notifications
  } catch (cause) {
    items.value = []
    error.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  draftTitle.value = ''
  draftContent.value = ''
  draftActive.value = 'active'
  password.value = ''
  formError.value = ''
  formOpen.value = true
}

function openEdit(item: AdminNotification) {
  editing.value = item
  draftTitle.value = item.title
  draftContent.value = item.content
  draftActive.value = item.active === false ? 'inactive' : 'active'
  password.value = ''
  formError.value = ''
  formOpen.value = true
}

async function submit() {
  if (saving.value) return
  const title = draftTitle.value.trim()
  const content = draftContent.value.trim()
  if (!title || !content || !password.value) {
    formError.value = t('manage.notify.required')
    return
  }
  const owner = session.profile
  if (!owner) return

  saving.value = true
  formError.value = ''
  try {
    const token = await verifyPassword(owner.id, password.value)
    const active = draftActive.value === 'active'
    if (editing.value) {
      const next = await updateNotification(editing.value.id, {
        title,
        content,
        active,
        verification_token: token
      })
      const index = items.value.findIndex((item) => item.id === next.id)
      if (index >= 0) items.value[index] = next
      ui.notify(t('manage.notify.updated'), 'success')
    } else {
      const created = await createNotification({
        title,
        content,
        verification_token: token
      })
      items.value = [created, ...items.value]
      ui.notify(t('manage.notify.published'), 'success')
    }
    formOpen.value = false
    password.value = ''
  } catch (cause) {
    if (cause instanceof ApiError) {
      formError.value =
        cause.status === 403 || cause.status === 401
          ? t('manage.notify.verifyFailed')
          : cause.message
    } else {
      formError.value = t('common.error')
    }
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="title">{{ t('manage.notify.title') }}</h1>
        <p class="sub">{{ t('manage.notify.subtitle') }}</p>
      </div>
      <ZButton size="sm" variant="primary" icon="plus" @click="openCreate">
        {{ t('manage.notify.create') }}
      </ZButton>
    </header>

    <ManageTabs />

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <ZCard padding="none">
      <div v-if="loading" class="rows">
        <div v-for="index in 4" :key="index" class="row">
          <ZSkeleton width="38%" height="0.95rem" />
          <ZSkeleton width="5rem" height="0.75rem" />
        </div>
      </div>

      <ul v-else-if="items.length" class="rows">
        <li v-for="item in items" :key="item.id" class="row">
          <div class="main">
            <div class="line">
              <strong>{{ item.title }}</strong>
              <ZBadge :tone="item.active === false ? 'neutral' : 'success'">
                {{ item.active === false ? t('manage.notify.inactive') : t('manage.notify.activeState') }}
              </ZBadge>
            </div>
            <p class="content">{{ preview(item.content) }}</p>
            <small class="meta">
              <span v-if="item.author_name">{{ t('manage.notify.author') }} {{ item.author_name }}</span>
              <span v-if="item.updated_at"> · {{ formatDateTime(item.updated_at) }}</span>
              <span class="num"> · v{{ item.version }}</span>
            </small>
          </div>
          <ZBadge v-if="item.source_ref" tone="info">{{ t('manage.notify.auto') }}</ZBadge>
          <ZButton v-else size="sm" variant="quiet" @click="openEdit(item)">
            {{ t('manage.notify.edit') }}
          </ZButton>
        </li>
      </ul>

      <ZEmpty v-else-if="!error" compact icon="bell" :title="t('manage.notify.empty')" />
    </ZCard>

    <ZModal
      v-model="formOpen"
      :title="isEditing ? t('manage.notify.editTitle') : t('manage.notify.createTitle')"
      size="md"
    >
      <div class="form">
        <ZField v-model="draftTitle" :label="t('manage.notify.titleLabel')" :disabled="saving" />
        <label class="editor-field">
          <span class="label">{{ t('manage.notify.contentLabel') }}</span>
          <MarkdownEditor v-model="draftContent" :disabled="saving" min-height="180px" />
        </label>
        <div class="state">
          <span class="label">{{ t('manage.notify.stateLabel') }}</span>
          <ZSegmented
            v-model="draftActive"
            :options="stateOptions"
            size="sm"
            :aria-label="t('manage.notify.stateLabel')"
          />
        </div>
        <ZField
          v-model="password"
          type="password"
          allow-reveal
          :label="t('manage.notify.password')"
          :hint="t('manage.notify.passwordHint')"
          :disabled="saving"
          autocomplete="current-password"
        />
        <ZAlert v-if="formError" tone="danger" :description="formError" />
      </div>

      <template #footer>
        <ZButton variant="ghost" size="sm" :disabled="saving" @click="formOpen = false">
          {{ t('common.cancel') }}
        </ZButton>
        <ZButton variant="primary" size="sm" :loading="saving" @click="submit">
          {{ t('manage.notify.save') }}
        </ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
}

.title {
  font-size: var(--text-3xl);
  font-weight: 500;
}

.sub {
  margin-top: var(--space-2);
  color: var(--ink-muted);
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.row:last-child {
  border-bottom: 0;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
}

.line {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.line strong {
  font-size: var(--text-sm);
  font-weight: 600;
}

.content {
  font-size: var(--text-xs);
  color: var(--ink-muted);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.meta {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.retry {
  margin-top: var(--space-3);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.label {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.editor-field,
.state {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

@media (max-width: 680px) {
  .row {
    flex-wrap: wrap;
  }
}
</style>

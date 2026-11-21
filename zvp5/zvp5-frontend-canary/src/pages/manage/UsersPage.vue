<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchUsers, setUserBanned, updateUserRoles } from '@/shared/api/manage'
import type { AdminUserRow, UserRole } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZAvatar from '@/shared/ui/ZAvatar.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ManageTabs from './ManageTabs.vue'

const { t } = useI18n()
const session = useSessionStore()
const ui = useUiStore()

const page = ref(1)
const size = 20
const total = ref(0)
const query = ref('')
const role = ref('')
const rows = ref<AdminUserRow[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)

const isAdmin = computed(() => session.isAdmin)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size)))

const roleOptions = computed(() => [
  { value: '', label: t('common.all') },
  { value: 'student', label: t('role.student') },
  { value: 'secretary', label: t('role.secretary') },
  { value: 'department', label: t('role.department') },
  { value: 'admin', label: t('role.admin') }
])

type EditableRole = 'student' | 'secretary' | 'department'
const editableRoles: EditableRole[] = ['student', 'secretary', 'department']

const editOpen = ref(false)
const target = ref<AdminUserRow | null>(null)
const draftRoles = ref<EditableRole[]>([])
const banReason = ref('')
const banDays = ref('permanent')

const isSelf = computed(() => target.value?._id === session.profile?._id)
const targetIsAdmin = computed(() => Boolean(target.value?.roles.includes('admin')))
const canMutate = computed(
  () => isAdmin.value && target.value !== null && !isSelf.value && !targetIsAdmin.value
)

const banOptions = computed(() => [
  { value: 'permanent', label: t('manage.users.permanent') },
  { value: '7', label: t('manage.users.days', { n: 7 }) },
  { value: '30', label: t('manage.users.days', { n: 30 }) }
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchUsers({
      page: page.value,
      size,
      query: query.value.trim(),
      role: isAdmin.value ? role.value : ''
    })
    rows.value = result.data
    total.value = result.total
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    loading.value = false
  }
}

function openEdit(row: AdminUserRow) {
  target.value = row
  draftRoles.value = editableRoles.filter((item) => row.roles.includes(item))
  banReason.value = ''
  banDays.value = 'permanent'
  editOpen.value = true
}

function toggleRole(value: EditableRole) {
  draftRoles.value = draftRoles.value.includes(value)
    ? draftRoles.value.filter((item) => item !== value)
    : [...draftRoles.value, value]
}

function report(cause: unknown) {
  if (cause instanceof ApiError) {
    if (cause.status === 403) {
      ui.notify(cause.message, 'danger')
      return
    }
    ui.notify(cause.message, 'danger')
    return
  }
  ui.notify(t('common.error'), 'danger')
}

async function saveRoles() {
  const row = target.value
  if (!row || saving.value) return
  const next: UserRole[] = [
    ...row.roles.filter((item) => item === 'admin'),
    ...draftRoles.value
  ]
  saving.value = true
  try {
    await updateUserRoles(row._id, next)
    row.roles = next
    ui.notify(t('manage.users.saved'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    saving.value = false
  }
}

async function ban() {
  const row = target.value
  if (!row || saving.value) return
  saving.value = true
  try {
    const payload: { banned: boolean; reason?: string; duration_days?: number } = {
      banned: true,
      reason: banReason.value.trim() || undefined
    }
    if (banDays.value !== 'permanent') payload.duration_days = Number(banDays.value)
    const result = await setUserBanned(row._id, payload)
    row.banned = result.banned
    ui.notify(t('manage.users.bannedDone'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    saving.value = false
  }
}

async function unban() {
  const row = target.value
  if (!row || saving.value) return
  saving.value = true
  try {
    const result = await setUserBanned(row._id, { banned: false })
    row.banned = result.banned
    ui.notify(t('manage.users.unbannedDone'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    saving.value = false
  }
}

let timer: number | undefined
function scheduleLoad() {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    page.value = 1
    void load()
  }, 320)
}

watch(query, scheduleLoad)
watch(role, () => {
  page.value = 1
  void load()
})
watch(page, () => void load())

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="title">{{ t('manage.users.title') }}</h1>
        <p class="sub">{{ t('manage.users.subtitle') }}</p>
      </div>
      <span class="range num">{{ total }}</span>
    </header>

    <ManageTabs />

    <ZAlert v-if="!isAdmin" tone="info" :description="t('manage.users.adminOnly')" />

    <div class="filters">
      <ZSegmented
        v-if="isAdmin"
        v-model="role"
        :options="roleOptions"
        size="sm"
        :aria-label="t('manage.users.roleFilter')"
      />
      <ZField
        v-model="query"
        class="search"
        type="search"
        icon="search"
        size="sm"
        :placeholder="t('manage.users.searchPlaceholder')"
      />
    </div>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <ZCard padding="none">
      <div v-if="loading" class="rows">
        <div v-for="index in 6" :key="index" class="row">
          <ZSkeleton circle width="38px" height="38px" />
          <ZSkeleton width="32%" height="0.9rem" />
          <ZSkeleton width="5rem" height="0.8rem" />
        </div>
      </div>

      <ul v-else-if="rows.length" class="rows">
        <li v-for="row in rows" :key="row._id" class="row">
          <ZAvatar :name="row.name" :size="38" :tone="row.banned ? 'ink' : 'warm'" />

          <div class="main">
            <strong>{{ row.name || '—' }}</strong>
            <small class="num">{{ row.id }} · {{ row.class_name || t('manage.users.unassigned') }}</small>
          </div>

          <div class="roles">
            <ZBadge v-for="item in row.roles" :key="item" :tone="item === 'admin' ? 'accent' : 'neutral'">
              {{ t(`role.${item}`) }}
            </ZBadge>
            <ZBadge v-if="row.banned" tone="danger" dot>{{ t('manage.users.bannedTag') }}</ZBadge>
          </div>

          <button
            type="button"
            class="edit"
            :aria-label="t('manage.users.edit')"
            @click="openEdit(row)"
          >
            <ZIcon name="pencil" :size="16" />
          </button>
        </li>
      </ul>

      <ZEmpty v-else-if="!error" compact icon="users" :title="t('manage.users.empty')" />
    </ZCard>

    <nav v-if="totalPages > 1" class="pager" aria-label="分页">
      <ZButton variant="ghost" size="sm" icon="chevron-left" :disabled="page <= 1 || loading" @click="page -= 1">
        {{ t('common.back') }}
      </ZButton>
      <span class="page-num num">{{ page }} / {{ totalPages }}</span>
      <ZButton
        variant="ghost"
        size="sm"
        icon-right="chevron-right"
        :disabled="page >= totalPages || loading"
        @click="page += 1"
      >
        {{ t('home.viewAll') }}
      </ZButton>
    </nav>

    <ZModal v-model="editOpen" :title="t('manage.users.editTitle')" size="md">
      <div v-if="target" class="sheet">
        <div class="identity">
          <ZAvatar :name="target.name" :size="52" :tone="target.banned ? 'ink' : 'warm'" />
          <div>
            <strong class="identity-name">{{ target.name }}</strong>
            <p class="identity-meta num">
              {{ target.id }} · {{ target.class_name || t('manage.users.unassigned') }}
            </p>
          </div>
        </div>

        <div class="block">
          <span class="block-label">{{ t('manage.users.roles') }}</span>
          <div class="role-picks">
            <button
              v-for="item in editableRoles"
              :key="item"
              type="button"
              class="pick"
              :class="{ 'is-on': draftRoles.includes(item) }"
              :disabled="!canMutate || saving"
              @click="toggleRole(item)"
            >
              <ZIcon v-if="draftRoles.includes(item)" name="check" :size="14" />
              {{ t(`role.${item}`) }}
            </button>
            <ZBadge v-if="targetIsAdmin" tone="accent">{{ t('role.admin') }}</ZBadge>
          </div>
          <ZButton
            v-if="canMutate"
            class="save-roles"
            size="sm"
            variant="secondary"
            :disabled="saving"
            @click="saveRoles"
          >
            {{ t('common.save') }}
          </ZButton>
        </div>

        <div class="block">
          <span class="block-label">{{ t('manage.users.banTitle') }}</span>
          <p class="block-hint">{{ t('manage.users.banDescription') }}</p>

          <template v-if="target.banned">
            <ZButton size="sm" variant="primary" :disabled="!canMutate || saving" @click="unban">
              {{ t('manage.users.unban') }}
            </ZButton>
          </template>

          <template v-else>
            <ZField
              v-model="banReason"
              size="sm"
              :label="t('manage.users.banReason')"
              :placeholder="t('manage.users.banReasonPlaceholder')"
              :disabled="!canMutate || saving"
            />
            <div class="ban-row">
              <ZSegmented
                v-model="banDays"
                :options="banOptions"
                size="sm"
                :aria-label="t('manage.users.banDuration')"
              />
              <ZButton
                size="sm"
                variant="primary"
                :disabled="!canMutate || saving"
                @click="ban"
              >
                {{ t('manage.users.banSubmit') }}
              </ZButton>
            </div>
          </template>

          <p v-if="!canMutate && isAdmin && isSelf" class="block-hint">{{ t('manage.users.selfBan') }}</p>
          <p v-else-if="!canMutate && isAdmin && targetIsAdmin" class="block-hint">
            {{ t('manage.users.adminBan') }}
          </p>
        </div>
      </div>

      <template #footer>
        <ZButton variant="ghost" size="sm" @click="editOpen = false">{{ t('common.close') }}</ZButton>
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

.range {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.search {
  flex: 1;
  min-width: 220px;
  max-width: 340px;
  margin-left: auto;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.row:last-child {
  border-bottom: 0;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  flex: 1;
  min-width: 0;
}

.main strong {
  font-size: var(--text-sm);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.roles {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.3rem;
  flex: none;
}

.edit {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 50%;
  color: var(--ink-faint);
  border: 1px solid color-mix(in srgb, var(--glass-border) 70%, transparent);
  background:
    linear-gradient(160deg, color-mix(in srgb, var(--glass-hi) 30%, transparent), transparent 55%),
    var(--glass-flat);
  box-shadow: inset 0 1px 0 var(--glass-hi);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-micro) var(--spring-snappy),
    box-shadow var(--dur-fast) var(--ease-out);
}

.edit:hover {
  background-color: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent-ink);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    0 8px 18px -12px rgba(40, 30, 16, 0.5);
  transform: translateY(-1px) scale(1.05);
}

.edit:active {
  transform: scale(0.9);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
}

.page-num {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.retry {
  margin-top: var(--space-3);
}

.sheet {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.identity {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.identity-name {
  font-size: var(--text-lg);
  font-weight: 600;
}

.identity-meta {
  margin-top: 0.2rem;
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.block-label {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.block-hint {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.55;
}

.role-picks {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.pick {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.8rem;
  border-radius: 999px;
  border: 1px solid var(--line);
  font-size: var(--text-sm);
  color: var(--ink-muted);
  background: var(--surface-raised);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

.pick.is-on {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
  background: color-mix(in srgb, var(--accent) 12%, var(--surface-raised));
  color: var(--accent-ink);
}

.pick:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.save-roles {
  align-self: flex-start;
}

.ban-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

@media (max-width: 680px) {
  .row {
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-3);
  }

  .main {
    flex-basis: calc(100% - 6rem);
  }

  .roles {
    order: 3;
    flex-basis: 100%;
  }

  .search {
    max-width: none;
    margin-left: 0;
  }

  .ban-row {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>

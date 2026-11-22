<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  addGroupMember,
  createGroup,
  fetchGroupMembers,
  fetchGroups,
  fetchUsers,
  removeGroupMember,
  renameGroup
} from '@/shared/api/manage'
import type { AdminUserRow, GroupMemberRow, GroupRow } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import AdaptivePagination from '@/shared/ui/pagination/AdaptivePagination.vue'
import { useAdaptivePagination } from '@/shared/composables/useAdaptivePagination'
import ManageTabs from './ManageTabs.vue'

const { t } = useI18n()
const session = useSessionStore()
const ui = useUiStore()

const page = ref(1)
const total = ref(0)
const query = ref('')
const rows = ref<GroupRow[]>([])
const loading = ref(true)
const error = ref('')
const listRef = ref<HTMLElement | null>(null)

const { size, auto, pageSizeOptions, setAuto, setPageSize } = useAdaptivePagination({
  listId: 'manage-groups',
  container: listRef,
  itemSelector: '.row',
  defaultPageSize: 20,
  gap: 0,
  min: 8,
  reserve: 96,
  fallbackItemHeight: 66,
  max: 80
})

const isAdmin = computed(() => session.isAdmin)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

const createOpen = ref(false)
const createName = ref('')
const createError = ref('')
const saving = ref(false)

const renameOpen = ref(false)
const renameTarget = ref<GroupRow | null>(null)
const renameValue = ref('')

const memberOpen = ref(false)
const memberGroup = ref<GroupRow | null>(null)
const members = ref<GroupMemberRow[]>([])
const memberTotal = ref(0)
const memberPage = ref(1)
const memberQuery = ref('')
const memberLoading = ref(false)
const memberBusy = ref<string | null>(null)
const confirmRemove = ref<string | null>(null)

const addQuery = ref('')
const candidates = ref<AdminUserRow[]>([])
const addLoading = ref(false)

const {
  size: memberSize,
  pageSizeOptions: memberSizeOptions,
  setPageSize: setMemberPageSize
} = useAdaptivePagination({
  listId: 'group-members',
  allowAuto: false,
  allowColumns: false,
  defaultPageSize: 12,
  min: 5,
  max: 100
})
const isPermissionGroup = computed(() => memberGroup.value?.type === 'permission')
const canEditMembers = computed(() => isAdmin.value && !isPermissionGroup.value)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchGroups({ page: page.value, size: size.value, query: query.value.trim() })
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

function report(cause: unknown) {
  ui.notify(cause instanceof ApiError ? cause.message : t('common.error'), 'danger')
}

async function submitCreate() {
  if (saving.value) return
  const name = createName.value.trim()
  if (!name) {
    createError.value = t('manage.groups.namePlaceholder')
    return
  }
  saving.value = true
  createError.value = ''
  try {
    await createGroup(name)
    createOpen.value = false
    createName.value = ''
    ui.notify(t('manage.groups.created'), 'success')
    page.value = 1
    await load()
  } catch (cause) {
    createError.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    saving.value = false
  }
}

function openRename(row: GroupRow) {
  renameTarget.value = row
  renameValue.value = row.name
  renameOpen.value = true
}

async function submitRename() {
  const target = renameTarget.value
  if (!target || saving.value) return
  const name = renameValue.value.trim()
  if (!name || name === target.name) {
    renameOpen.value = false
    return
  }
  saving.value = true
  try {
    await renameGroup(target._id, name)
    target.name = name
    renameOpen.value = false
    ui.notify(t('manage.groups.renamed'), 'success')
  } catch (cause) {
    report(cause)
  } finally {
    saving.value = false
  }
}

async function loadMembers() {
  const group = memberGroup.value
  if (!group) return
  memberLoading.value = true
  try {
    const result = await fetchGroupMembers(
      group._id,
      memberPage.value,
      memberSize.value,
      memberQuery.value.trim()
    )
    members.value = result.data
    memberTotal.value = result.total
    const maxPage = Math.max(1, Math.ceil(result.total / memberSize.value))
    if (memberPage.value > maxPage) memberPage.value = maxPage
  } catch {
    members.value = []
    memberTotal.value = 0
  } finally {
    memberLoading.value = false
  }
}

function openMembers(row: GroupRow) {
  memberGroup.value = row
  memberPage.value = 1
  memberQuery.value = ''
  addQuery.value = ''
  candidates.value = []
  confirmRemove.value = null
  memberOpen.value = true
  void loadMembers()
}

async function searchCandidates() {
  const keyword = addQuery.value.trim()
  if (!keyword || !canEditMembers.value) {
    candidates.value = []
    return
  }
  addLoading.value = true
  try {
    const result = await fetchUsers({ page: 1, size: 6, query: keyword, role: '' })
    const existing = new Set(members.value.map((item) => item._id))
    candidates.value = result.data.filter((row) => !existing.has(row._id))
  } catch {
    candidates.value = []
  } finally {
    addLoading.value = false
  }
}

async function addMember(user: AdminUserRow) {
  const group = memberGroup.value
  if (!group || memberBusy.value) return
  memberBusy.value = user._id
  try {
    await addGroupMember(group._id, user._id)
    candidates.value = candidates.value.filter((item) => item._id !== user._id)
    addQuery.value = ''
    ui.notify(t('manage.groups.memberAdded'), 'success')
    await loadMembers()
  } catch (cause) {
    report(cause)
  } finally {
    memberBusy.value = null
  }
}

async function removeMember(member: GroupMemberRow) {
  const group = memberGroup.value
  if (!group || memberBusy.value) return
  memberBusy.value = member._id
  try {
    await removeGroupMember(group._id, member._id)
    confirmRemove.value = null
    ui.notify(t('manage.groups.memberRemoved'), 'success')
    await loadMembers()
  } catch (cause) {
    report(cause)
  } finally {
    memberBusy.value = null
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

let memberTimer: number | undefined
watch(memberQuery, () => {
  window.clearTimeout(memberTimer)
  memberTimer = window.setTimeout(() => {
    memberPage.value = 1
    void loadMembers()
  }, 320)
})

let addTimer: number | undefined
watch(addQuery, () => {
  window.clearTimeout(addTimer)
  addTimer = window.setTimeout(() => void searchCandidates(), 320)
})

watch(page, () => void load())
watch(query, scheduleLoad)
watch(memberPage, () => void loadMembers())
watch(size, () => {
  if (page.value !== 1) {
    page.value = 1
    return
  }
  void load()
})
watch(memberSize, () => {
  if (memberPage.value !== 1) {
    memberPage.value = 1
    return
  }
  if (memberOpen.value) void loadMembers()
})

onMounted(load)
onBeforeUnmount(() => {
  window.clearTimeout(timer)
  window.clearTimeout(memberTimer)
  window.clearTimeout(addTimer)
})
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="title">{{ t('manage.groups.title') }}</h1>
        <p class="sub">{{ t('manage.groups.subtitle') }}</p>
      </div>
      <ZButton v-if="isAdmin" size="sm" variant="primary" icon="plus" @click="createOpen = true">
        {{ t('manage.groups.create') }}
      </ZButton>
    </header>

    <ManageTabs />

    <div class="filters">
      <ZField
        v-model="query"
        class="search"
        type="search"
        icon="search"
        size="sm"
        :placeholder="t('manage.groups.searchPlaceholder')"
      />
    </div>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <ZCard padding="none">
      <div v-if="loading && !rows.length" ref="listRef" class="rows">
        <div v-for="index in 5" :key="index" class="row">
          <ZSkeleton width="28%" height="0.95rem" />
          <ZSkeleton width="4rem" height="0.75rem" />
        </div>
      </div>

      <ul v-else-if="rows.length" ref="listRef" class="rows">
        <li v-for="row in rows" :key="row._id" class="row">
          <button type="button" class="main" @click="openMembers(row)">
            <strong>{{ row.name }}</strong>
            <small v-if="row.description">{{ row.description }}</small>
          </button>

          <ZBadge :tone="row.type === 'permission' ? 'info' : 'neutral'">
            {{ row.type === 'permission' ? t('manage.groups.typePermission') : t('manage.groups.typeClass') }}
          </ZBadge>

          <ZButton v-if="isAdmin" size="sm" variant="quiet" @click="openRename(row)">
            {{ t('manage.groups.rename') }}
          </ZButton>
          <ZButton size="sm" variant="quiet" icon-right="chevron-right" @click="openMembers(row)">
            {{ t('manage.groups.members') }}
          </ZButton>
        </li>
      </ul>

      <ZEmpty v-else-if="!error" compact icon="users" :title="t('manage.groups.empty')" />
    </ZCard>

    <AdaptivePagination
      :page="page"
      :page-size="size"
      :total="total"
      :auto="auto"
      :page-size-options="pageSizeOptions"
      :disabled="loading"
      @update:page="page = $event"
      @update:page-size="setPageSize"
      @update:auto="setAuto"
    />

    <ZModal v-model="createOpen" :title="t('manage.groups.createTitle')" size="sm">
      <ZField
        v-model="createName"
        :label="t('manage.groups.nameLabel')"
        :placeholder="t('manage.groups.namePlaceholder')"
        :error="createError"
        :disabled="saving"
        @enter="submitCreate"
      />
      <template #footer>
        <ZButton variant="ghost" size="sm" @click="createOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" size="sm" :disabled="saving" @click="submitCreate">
          {{ t('common.save') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="renameOpen" :title="t('manage.groups.renameTitle')" size="sm">
      <ZField
        v-model="renameValue"
        :label="t('manage.groups.nameLabel')"
        :placeholder="t('manage.groups.namePlaceholder')"
        :disabled="saving"
        @enter="submitRename"
      />
      <template #footer>
        <ZButton variant="ghost" size="sm" @click="renameOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" size="sm" :disabled="saving" @click="submitRename">
          {{ t('common.save') }}
        </ZButton>
      </template>
    </ZModal>

    <ZModal v-model="memberOpen" :title="memberGroup?.name ?? t('manage.groups.memberTitle')" size="lg">
      <div class="member-sheet">
        <p v-if="isPermissionGroup" class="block-hint">{{ t('manage.groups.permissionGroupHint') }}</p>

        <ZField
          v-model="memberQuery"
          type="search"
          icon="search"
          size="sm"
          :placeholder="t('manage.groups.memberSearch')"
        />

        <div v-if="canEditMembers" class="add-box">
          <ZField
            v-model="addQuery"
            type="search"
            icon="plus"
            size="sm"
            :placeholder="t('manage.groups.addPlaceholder')"
            :hint="addQuery.trim() ? undefined : t('manage.groups.addHint')"
          />
          <ul v-if="candidates.length" class="candidates">
            <li v-for="user in candidates" :key="user._id">
              <span class="candidate-name">{{ user.name }}</span>
              <span class="candidate-meta num">{{ user.id }}</span>
              <ZButton size="sm" variant="secondary" :disabled="memberBusy === user._id" @click="addMember(user)">
                {{ t('manage.groups.addMember') }}
              </ZButton>
            </li>
          </ul>
        </div>

        <div v-if="memberLoading" class="rows">
          <div v-for="index in 4" :key="index" class="row">
            <ZSkeleton width="30%" height="0.85rem" />
            <ZSkeleton width="4rem" height="0.75rem" />
          </div>
        </div>

        <ul v-else-if="members.length" class="rows">
          <li v-for="member in members" :key="member._id" class="row">
            <span class="member-name">
              <span class="avatar" aria-hidden="true">{{ Array.from(member.name || '·')[0] }}</span>
              <span class="member-text">
                <strong>{{ member.name || '—' }}</strong>
                <small class="num">{{ member.id }}</small>
              </span>
            </span>
            <div class="member-roles">
              <ZBadge v-for="item in member.roles" :key="item" :tone="item === 'admin' ? 'accent' : 'neutral'">
                {{ t(`role.${item}`) }}
              </ZBadge>
              <ZBadge v-if="member.banned" tone="danger">{{ t('manage.users.bannedTag') }}</ZBadge>
            </div>
            <button
              v-if="canEditMembers && confirmRemove !== member._id"
              type="button"
              class="remove"
              :aria-label="t('manage.groups.removeMember')"
              :disabled="memberBusy === member._id"
              @click="confirmRemove = member._id"
            >
              <ZIcon name="x" :size="15" />
            </button>
            <ZButton
              v-else-if="canEditMembers"
              size="sm"
              variant="danger"
              :disabled="memberBusy === member._id"
              @click="removeMember(member)"
            >
              {{ t('common.confirm') }}
            </ZButton>
          </li>
        </ul>

        <ZEmpty v-else compact icon="users" :title="t('manage.groups.memberEmpty')" />

        <AdaptivePagination
          :page="memberPage"
          :page-size="memberSize"
          :total="memberTotal"
          :auto="false"
          :allow-auto="false"
          :page-size-options="memberSizeOptions"
          :disabled="memberLoading"
          @update:page="memberPage = $event"
          @update:page-size="setMemberPageSize"
        />
      </div>

      <template #footer>
        <ZButton variant="ghost" size="sm" @click="memberOpen = false">{{ t('common.close') }}</ZButton>
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
  align-items: flex-start;
  gap: 0.15rem;
  flex: 1;
  min-width: 0;
  text-align: left;
  color: inherit;
}

.main strong {
  font-size: var(--text-sm);
  font-weight: 600;
}

.main small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

}
</style>

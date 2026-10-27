<script setup lang="ts">
import { ZButtonOrCard, ZActivityMember, ZActivityDuration } from '@/components'
import type { ActivityMember, Activity } from '@/../types/v2'
import { toRefs, watch } from 'vue'
import { User as IconUser, Minus, EditPen, Search } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useI18n } from 'vue-i18n'
import { ElTable, ElTableColumn, ElButton, ElIcon, ElEmpty, ElMessageBox, ElInput } from 'element-plus'
import { useDebounceFn, useWindowSize } from '@vueuse/core'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'

const user = useUserStore()
const router = useRouter()
const { t } = useI18n()
const { height } = useWindowSize()
const open = ref(false)
const max = ref(height.value * 0.5)

watch(height, () => {
  max.value = height.value * 0.5
})

const props = withDefaults(
  defineProps<{
    activity: Activity
    membersCount?: number
    mode?: 'button' | 'card' | 'inline'
    wholesale?: boolean
    modelValue?: ActivityMember[]
    selectable?: boolean
  }>(),
  {
    mode: 'button',
    wholesale: false,
    membersCount: 0,
    selectable: false
  }
)
const emits = defineEmits<{
  refresh: []
  'update:modelValue': [ActivityMember[]]
}>()

const { activity, mode, wholesale, membersCount, modelValue, selectable } = toRefs(props)
const modified = ref(false)
const members = ref<(ActivityMember & { member_name?: string })[]>([])
const page = ref(1)
const perpage = ref(10)
const search = ref('')
const size = ref(membersCount.value)
const pageLoading = ref(false)

const usersSelected = ref<ActivityMember[]>(modelValue.value ?? [])

function handleSelectionChange(val: ActivityMember[]) {
  emits('update:modelValue', val)
}

watch(
  usersSelected,
  (newVal) => {
    emits('update:modelValue', newVal)
  },
  { deep: true, immediate: true }
)

function goRegister() {
  const id = activity.value._id
  if (!id) return
  void router.push({ path: '/activity/register', query: { activity: id } })
}

let requestId = 0

async function refreshMembers() {
  const current = ++requestId
  pageLoading.value = true
  try {
    const { members: membersResult, total } = await api.activity.member.reads_v3(
      activity.value._id,
      page.value,
      perpage.value,
      search.value
    )
    if (current !== requestId) return
    members.value = membersResult
    size.value = total
    if (mode.value === 'inline' && modified.value) {
      modified.value = false
      emits('refresh')
    }
  } finally {
    if (current === requestId) pageLoading.value = false
  }
}

function refreshFromFirstPage() {
  if (page.value === 1) {
    void refreshMembers()
    return
  }
  page.value = 1
}

const debouncedRefresh = useDebounceFn(refreshFromFirstPage, 300)

refreshMembers()

const loading = ref('')

const memberFunctions = {
  async remove(id: string) {
    modified.value = true
    loading.value = id
    await api.activity.member.remove(id, activity.value._id, user._id).catch(() => {
      loading.value = ''
    })
    loading.value = ''
    await refreshMembers()
  }
}

// 删除成员是不可恢复的写操作，先弹危险确认；取消则不发请求。
async function confirmRemove(sessionId: string) {
  try {
    await ElMessageBox.confirm(
      t('activity.member.dialog.actions.removeMessage'),
      t('activity.member.dialog.actions.removeTitle'),
      {
        type: 'warning',
        confirmButtonText: t('activity.member.dialog.actions.remove'),
        cancelButtonText: t('activity.member.dialog.actions.cancel'),
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  await memberFunctions.remove(sessionId)
}

watch(open, () => {
  // refreshMembers()
  if (open.value) {
    modified.value = false
  }
  if (!open.value && modified.value) emits('refresh')
})

const show = ref(false)

show.value = true

watch(page, refreshMembers)
watch(perpage, refreshFromFirstPage)
watch(search, debouncedRefresh)
</script>

<template>
  <ZButtonOrCard
    v-model:open="open"
    :mode="mode === 'inline' ? 'card' : mode"
    pop-type="dialog"
    width="80%"
    size="small"
    :icon="IconUser"
    round
    type="danger"
    :button-loading="pageLoading"
    :title="t('activity.member.dialog.title', { name: activity.name })"
  >
    <template #text> {{ size }} {{ t('activity.units.person', membersCount) }} </template>
    <template #default>
      <div
        v-if="mode === 'inline'"
        class="pb-2 flex items-center justify-between flex-wrap gap-y-1"
      >
        <p class="flex items-center gap-1 font-medium">
          <ElIcon><IconUser /></ElIcon>
          {{ t('activity.member.inline.title') }}
        </p>
        <div class="flex items-center gap-2 flex-wrap">
          <ElInput
            v-model="search"
            :placeholder="t('activity.member.dialog.search')"
            :prefix-icon="Search"
            clearable
            size="small"
            style="width: 240px"
          />
          <span class="text-sm text-gray-500" style="font-variant-numeric: tabular-nums">
            {{ t('activity.member.inline.count', { count: size }) }}
          </span>
        </div>
      </div>
      <ElInput
        v-if="mode !== 'inline'"
        v-model="search"
        :placeholder="t('activity.member.dialog.search')"
        :prefix-icon="Search"
        clearable
        size="small"
        class="pb-2"
        style="width: 240px"
      />
      <div v-if="show">
        <ElTable
          v-loading="pageLoading"
          :data="members"
          stripe
          :height="mode === 'inline' ? undefined : max"
          row-key="member"
          @selection-change="handleSelectionChange"
        >
          <ElTableColumn v-if="selectable" type="selection" width="55" reserve-selection />
          <ElTableColumn prop="_id" :label="t('activity.member.name')">
            <template #default="scope">
              <ZActivityMember
                :id="scope.row.member"
                :person="scope.row.user"
                :aid="activity._id"
                :member-session="scope.row._id"
                :member-status="scope.row.status"
                @refresh="refreshMembers"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn prop="duration" :label="t('activity.form.duration')">
            <template #default="scope">
              <ZActivityDuration
                v-model:duration="scope.row.duration"
                :mode="scope.row.mode"
                :id="activity._id"
                :uid="scope.row.member"
                :record="scope.row._id"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn
            fixed="right"
            v-if="
              (user.position.includes('admin') ||
                user.position.includes('department')) &&
              !wholesale &&
              !selectable
            "
            class="no-print"
          >
            <template #header>
              <ElButton type="primary" round size="small" :icon="EditPen" @click="goRegister">
                {{ t('nav.register') }}
              </ElButton>
            </template>
            <template #default="scope">
              <ElButton
                text
                bg
                round
                size="small"
                type="danger"
                class="no-print"
                :loading="loading === scope.row._id"
                :icon="Minus"
                @click="confirmRemove(scope.row._id)"
              >
                {{ t('activity.member.dialog.actions.remove') }}
              </ElButton>
            </template>
          </ElTableColumn>
          <template #empty v-if="mode === 'inline'">
            <ElEmpty :description="t('activity.member.inline.empty')" :image-size="80" />
          </template>
        </ElTable>
      </div>
      <div class="py-2 text-center" v-if="size !== 0">
        <ZPagination
          v-model:current-page="page"
          v-model:page-size="perpage"
          :total="size"
        />
      </div>
    </template>
  </ZButtonOrCard>
</template>

<style scoped>
@media print {
  .no-print {
    display: none !important;
  }
}
</style>

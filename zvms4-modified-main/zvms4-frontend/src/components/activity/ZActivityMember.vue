<script setup lang="ts">
import type { ErrorResponse, User } from '@/../types'
import type { ActivityMember } from '@/../types/v2'
import { computed, toRefs, ref, watch } from 'vue'
import type { Component as VueComponent } from 'vue'
import { Plus, Refresh, User as UserIcon } from '@element-plus/icons-vue'
import { ZButtonOrCard, ZUserTimeJudge } from '@/components'
import api from '@/api'
import {
  ElButton,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElMessage,
  ElMessageBox,
  ElNotification,
  ElPopconfirm,
  ElPopover,
  ElSpace,
  ElTag
} from 'element-plus'
import { useI18n } from 'vue-i18n'
import ZUserGroup from '../tags/ZUserGroup.vue'
import ZUserPosition from '../tags/ZUserPosition.vue'
import { useUserStore } from '@/stores/user'
import { temporaryToken } from '@/plugins/short-token'
import { useClipboard, useWindowSize } from '@vueuse/core'
import router from '@/router'

const { width, height } = useWindowSize()
const { t } = useI18n()

type PersonLike = Pick<User, '_id' | 'id' | 'name' | 'group' | 'roles' | 'banned'> & {
  sex?: string
  class_id?: string
  past?: string[]
}

const props = withDefaults(
  defineProps<{
    id: string
    person?: PersonLike
    mode?: 'card' | 'button'
    icon?: VueComponent
    color?: 'primary' | 'success' | 'danger' | 'warning' | 'info'
    aid?: string
    memberSession?: string
    memberStatus?: ActivityMember['status']
  }>(),
  {
    icon: UserIcon,
    color: 'primary',
    mode: 'button'
  }
)
const { id, person: personProp, icon, color, mode } = toRefs(props)

const emits = defineEmits<{
  refresh: []
}>()
const personData = ref<PersonLike>()
const loading = ref(true)
const loading2 = ref(false)
const error = ref(false)
const userStore = useUserStore()
const past = ref<string[]>([])
const notFound = ref(false)

watch([id, personProp], () => {
  syncPerson()
})

function syncPerson() {
  if (personProp.value) {
    personData.value = personProp.value
    past.value = personProp.value.past ?? []
    error.value = false
    notFound.value = false
    loading.value = false
    return
  }
  refresh()
}

function refresh() {
  loading.value = true
  notFound.value = false
  if (id.value)
    api.user
      .readOne(id.value)
      .then((res) => {
        if (!res) error.value = true
        else {
          personData.value = res
          past.value = res.past ?? []
        }
        loading.value = false
      })
      .catch((err: ErrorResponse) => {
        console.log(err, err.code)
        if (err.code === 404) {
          notFound.value = true
        }
        error.value = true
        loading.value = false
      })
  else {
    personData.value = undefined
    notFound.value = true
    loading.value = false
  }
}

syncPerson()

// 重置成功后的一次性初始密码弹窗：明文只在此响应中出现，关闭后无法再查看
const initialPasswordVisible = ref(false)
const initialPassword = ref('')
const { copy: copyToClipboard, copied: passwordCopied } = useClipboard({
  copiedDuring: 2000
})

async function resetMemberPassword() {
  loading2.value = true
  const token = await temporaryToken(userStore._id).catch(() => {
    loading2.value = false
  })
  if (!token) {
    return
  }
  const password = await api.user.password.reset(id.value, token).catch(() => {
    loading2.value = false
  })
  loading2.value = false
  if (password) {
    initialPassword.value = password
    initialPasswordVisible.value = true
  } else {
    // 兼容未返回一次性密码的旧契约：仅提示重置成功
    ElNotification({
      title: '密码重置成功',
      type: 'success'
    })
  }
}

async function copyInitialPassword() {
  if (!initialPassword.value) return
  await copyToClipboard(initialPassword.value).catch(() => {})
}

const vert = ref(width.value < height.value * 1.2)

watch(width, () => {
  vert.value = width.value < height.value * 1.2
})

watch(height, () => {
  vert.value = width.value < height.value * 1.2
})

const localStatus = ref<ActivityMember['status'] | undefined>(props.memberStatus)
watch(
  () => props.memberStatus,
  (status) => {
    localStatus.value = status
  }
)

const currentStatus = computed(() => localStatus.value ?? props.memberStatus)
const canReview = computed(
  () =>
    !!props.aid &&
    !!props.memberSession &&
    (userStore.position.includes('admin') || userStore.position.includes('department'))
)

const reviewVisible = ref(false)
const reviewing = ref<ActivityMember['status'] | null>(null)
const reviewOptions: ActivityMember['status'][] = ['effective', 'pending', 'refused']

const reviewTagType = {
  effective: 'success',
  pending: 'warning',
  refused: 'danger'
} as const

const reviewLabel = computed(() => t(`activity.member.review.status.${currentStatus.value}`))

async function modifyMemberStatus(status: ActivityMember['status']) {
  if (!props.aid || !props.memberSession) return
  if (status === currentStatus.value) {
    reviewVisible.value = false
    return
  }
  reviewing.value = status
  try {
    await api.activity.member.modifyStatus(props.aid, props.memberSession, status)
  } catch {
    return
  } finally {
    reviewing.value = null
  }
  localStatus.value = status
  reviewVisible.value = false
  ElMessage.success(t('activity.member.review.success'))
  emits('refresh')
}

async function removeUserPast(tag: string) {
  const index = past.value.indexOf(tag)
  if (index === -1) return
  try {
    await ElMessageBox.confirm(
      t('activity.member.past.removeMessage'),
      t('activity.member.past.removeTitle'),
      {
        type: 'warning',
        confirmButtonText: t('activity.member.past.remove'),
        cancelButtonText: t('activity.member.past.cancel'),
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  await api.user.past.delete(id.value, index)
  past.value.splice(index, 1)
}

/*
const inputVisible = ref(false)

const inputValue = ref('')

async function insertUserPast() {
  const value = inputValue.value
  if (value && past.value.indexOf(value) === -1) {
    inputValue.value = ''
    inputVisible.value = false
    await api.user.past.insert(id.value, value)
    past.value.push(value)
  }
}
*/
</script>

<template>
  <ElButton v-if="notFound && mode === 'button'" text bg type="danger" :icon="icon" size="small" round>
    {{ t('manage.personalPanel.notFound') }}
  </ElButton>
  <ZButtonOrCard
    v-else
    :mode="mode"
    :type="color"
    size="small"
    round
    :loading="loading"
    :icon="icon"
    :title="personData?.name"
    pop-type="dialog"
    :width="vert ? '94%' : 'min(720px, 60vw)'"
    v-bind="$attrs"
  >
    <template #default>
      <ElDescriptions
        class="fill"
        border
        :direction="vert ? 'vertical' : 'horizontal'"
        :column="vert ? 2 : undefined"
      >
        <template #title>
          <slot name="title" />
        </template>
        <template #extra>
          <slot name="extra" />
        </template>
        <ElDescriptionsItem :label="t('home.labels.name')">{{ personData?.name }}</ElDescriptionsItem>
        <ElDescriptionsItem :label="t('home.labels.number')">{{ personData?.id }}</ElDescriptionsItem>
        <ElDescriptionsItem :label="t('home.labels.identify')">
          <ZUserGroup
            v-for="group in personData?.group"
            :key="group"
            class="px-1"
            :group="group"
            :grouping="false"
          />
          <ZUserPosition
            v-if="personData && ((personData.roles ?? []).find((role) => role !== 'student') || !personData.group.length)"
            :position="personData.roles ?? []"
          />
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('home.labels.status')">
          <span v-if="!personData?.banned">{{ t('home.status.normal') }}</span>
          <ElTag v-else type="danger" size="small">{{ t('home.status.banned') }}</ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem v-if="memberStatus" :label="t('activity.member.review.label')">
          <ElPopover
            v-if="canReview"
            :title="t('activity.member.review.title', { name: personData?.name })"
            trigger="click"
            width="384px"
            v-model:visible="reviewVisible"
            placement="right"
          >
            <template #reference>
              <ElTag
                :type="reviewTagType[currentStatus ?? 'pending']"
                size="small"
                class="member-review__tag"
              >
                {{ reviewLabel }}
              </ElTag>
            </template>
            <div class="flex justify-end py-1">
              <ElSpace>
                <ElPopconfirm
                  v-for="option in reviewOptions"
                  :key="option"
                  :title="t('activity.member.review.confirm')"
                  width="256px"
                  @confirm="() => modifyMemberStatus(option)"
                >
                  <template #reference>
                    <ElButton
                      :type="option === 'effective' ? 'primary' : undefined"
                      text
                      bg
                      size="small"
                      :loading="reviewing === option"
                      :disabled="option === currentStatus || reviewing !== null"
                    >
                      {{ t('activity.member.review.' + option) }}
                    </ElButton>
                  </template>
                </ElPopconfirm>
              </ElSpace>
            </div>
          </ElPopover>
          <ElTag v-else :type="reviewTagType[currentStatus ?? 'pending']" size="small">
            {{ reviewLabel }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem
          :label="t('home.labels.past')"
          v-if="(userStore.position.includes('admin') || userStore.position.includes('department')) && past.length"
        >
          <div class="flex gap-2">
            <ElTag
              v-for="tag in past"
              :key="tag"
              effect="plain"
              round
              class="px-3"
              closable
              :disable-transitions="false"
              @close="removeUserPast(tag)"
            >
              {{ tag }}
            </ElTag>
            <!--<ElInput
              v-if="inputVisible"
              ref="InputRef"
              v-model.trim="inputValue"
              class="w-20 px-2"
              size="small"
              @keyup.enter="insertUserPast"
              @blur="insertUserPast"
            />
            <ElButton
              v-else
              class="button-new-tag px-2"
              text
              bg
              circle
              size="small"
              :icon="Plus"
              @click="inputVisible = true"
            />-->
          </div>
        </ElDescriptionsItem>
      </ElDescriptions>
      <ZUserTimeJudge
        class="py-2"
        v-if="userStore.position.includes('department') || userStore.position.includes('admin') || userStore.position.includes('secretary') && personData?.group[0] === userStore.class_id"
        :user="id"
        discount
      />
      <ElButton
        v-if="
          mode === 'button' &&
          (userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && personData?.group[0] === userStore.class_id)
        "
        text
        bg
        type="success"
        @click="router.push(`/user/${id}`)"
        class="w-full"
      >
        {{ t('manage.groupDetails.userList.open') }}
      </ElButton>
      <ElButton
        v-if="
          (userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && personData?.group[0] === userStore.class_id) &&
          id &&
          id !== userStore._id &&
          mode === 'card'
        "
        type="danger"
        text
        bg
        class="w-full"
        :icon="Refresh"
        @click="resetMemberPassword"
        :loading="loading2"
      >
        {{ t('manage.personalPanel.resetPassword') }}
      </ElButton>
    </template>
    <template #text>
      {{ personData?.name }}
    </template>
  </ZButtonOrCard>
  <!-- 一次性初始密码弹窗：密码明文只显示这一次，必须点「我已记下」关闭 -->
  <ElDialog
    v-model="initialPasswordVisible"
    :title="t('manage.personalPanel.initialPasswordDialog.title')"
    width="420px"
    align-center
    :close-on-click-modal="false"
  >
    <p class="text-sm leading-6 opacity-80">
      {{ t('manage.personalPanel.initialPasswordDialog.notice') }}
    </p>
    <div class="mt-4 flex items-center gap-2">
      <code
        class="min-w-0 flex-1 select-all break-all rounded bg-gray-100 px-3 py-2 font-mono text-base font-bold tracking-wider dark:bg-gray-800"
        >{{ initialPassword }}</code
      >
      <ElButton
        class="flex-none"
        :type="passwordCopied ? 'success' : 'primary'"
        @click="copyInitialPassword"
      >
        {{
          passwordCopied
            ? t('manage.personalPanel.initialPasswordDialog.copied')
            : t('manage.personalPanel.initialPasswordDialog.copy')
        }}
      </ElButton>
    </div>
    <template #footer>
      <ElButton
        type="primary"
        @click="initialPasswordVisible = false"
      >
        {{ t('manage.personalPanel.initialPasswordDialog.acknowledge') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.member-review__tag {
  cursor: pointer;
}
</style>

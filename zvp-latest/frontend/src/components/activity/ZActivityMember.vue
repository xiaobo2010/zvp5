<script setup lang="ts">
import type { User } from '@/types'
import { toRefs, ref, watch } from 'vue'
import type { Component as VueComponent } from 'vue'
import { Plus, Refresh, User as UserIcon } from '@element-plus/icons-vue'
import ZButtonOrCard from '@/components/utils/ZButtonOrCard.vue'
import ZUserTimeJudge from './ZUserTimeJudge.vue'
import api from '@/api'
import { ElMessage, ElDescriptions, ElDescriptionsItem, ElButton } from 'element-plus'
import { useUserStore } from '@/stores'
import { temporaryToken } from '@/api/user/temp-token'
import { useWindowSize } from '@vueuse/core'
import { useRouter } from 'vue-router'

const router = useRouter()

const { width, height } = useWindowSize()

const props = withDefaults(
  defineProps<{
    id: string
    mode?: 'card' | 'button'
    icon?: VueComponent
    color?: 'primary' | 'success' | 'danger' | 'warning' | 'info'
  }>(),
  {
    icon: UserIcon,
    color: 'primary',
    mode: 'button'
  }
)
const { id, icon, color, mode } = toRefs(props)
const person = ref<User>()
const loading = ref(true)
const loading2 = ref(false)
const error = ref(false)
const userStore = useUserStore()
const past = ref<string[]>([])
const notFound = ref(false)

watch(id, () => {
  refresh()
})

function refresh() {
  loading.value = true
  notFound.value = false
  if (id.value)
    api.user
      .readOne(id.value)
      .then((res) => {
        if (!res) error.value = true
        else {
          person.value = res as User
          past.value = person.value.past
        }
        loading.value = false
      })
      .catch((err) => {
        console.log(err, err.code)
        if (err.code === 404) {
          notFound.value = true
        }
        error.value = true
        loading.value = false
      })
  else {
    person.value = undefined
    notFound.value = true
    loading.value = false
  }
}

refresh()

async function resetMemberPassword() {
  loading2.value = true
  const user = person.value
  const password = user?.id?.toString()
  //if (password) {
    const token = await temporaryToken(userStore._id).catch(() => {
      loading2.value = false
    })
    if (!token) {
      return
    }
    await api.user.password.reset(id.value, /*password,*/ token, true).then(() => {
      ElMessage({
        message: '密码重置成功',
        type: 'success'
      })
    }).catch(() => {
      loading2.value = false
    })
  //}
  loading2.value = false
}

const vert = ref(width.value < height.value * 1.2)

watch(width, () => {
  vert.value = width.value < height.value * 1.2
})

watch(height, () => {
  vert.value = width.value < height.value * 1.2
})

async function removeUserPast(tag: string) {
  const index = past.value.indexOf(tag)
  if (index > -1) {
    await api.user.past.delete(id.value, index)
    past.value.splice(index, 1)
  }
}
</script>

<template>
  <ElButton v-if="notFound && mode === 'button'" text bg type="danger" :icon="icon" size="small" round>
    无此用户
  </ElButton>
  <ZButtonOrCard
    v-else
    :mode="mode"
    :type="color"
    size="small"
    round
    :loading="loading"
    :icon="icon"
    :title="person?.name"
    pop-type="dialog"
    width="50%"
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
        <ElDescriptionsItem label="姓名">{{ person?.name }}</ElDescriptionsItem>
        <ElDescriptionsItem label="学号">{{ person?.id }}</ElDescriptionsItem>
      </ElDescriptions>
      <ZUserTimeJudge
        class="py-2"
        v-if="userStore.position.includes('department') || userStore.position.includes('admin') || userStore.position.includes('secretary') && person?.group[0] === userStore.class_id"
        :user="id"
        discount
      />
      <ElButton
        v-if="
          mode === 'button' &&
          (userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && person?.group[0] === userStore.class_id)
        "
        text
        bg
        type="success"
        @click="router.push(`/user/${id}`)"
        class="w-full"
      >
        查看详情
      </ElButton>
      <ElButton
        v-if="
          (userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && person?.group[0] === userStore.class_id) &&
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
        重置密码
      </ElButton>
    </template>
    <template #text>
      {{ person?.name }}
    </template>
  </ZButtonOrCard>
</template>

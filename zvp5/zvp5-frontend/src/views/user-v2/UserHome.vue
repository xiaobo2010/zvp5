<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { useHomeTime } from '@/composables/useHomeTime'
import api from '@/api'
import HomeOverview from '@/components/home/HomeOverview.vue'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'
import ZUserPosition from '@/components/tags/ZUserPosition.vue'
import UserHomeWorklist from '@/components/user/UserHomeWorklist.vue'

const ZDetailedAnalysis = defineAsyncComponent(
  () => import('@/components/activity/ZDetailedAnalysis.vue')
)
const user = useUserStore()
const router = useRouter()
const { t } = useI18n()
useHeaderStore().setHeader(t('nav.home'))
const reportPage = ref(false)
const { progress, sums, loading, error, refresh } = useHomeTime(
  () => (user.isLogin ? user._id : ''),
  { read: api.user.time.read, save: (value) => user.setTime(value) }
)
const showWorklist = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)
watch(
  () => user.isLogin,
  (value) => {
    if (!value) void router.replace('/user/login')
  },
  { immediate: true }
)
watch(
  () => user._id,
  () => {
    reportPage.value = false
  }
)
</script>

<template>
  <div v-if="user.isLogin">
    <HomeOverview
      :name="user.name"
      :number="user.id"
      :user-key="user._id"
      :progress="progress"
      :loading="loading"
      :error="error"
      @refresh="refresh"
      @details="reportPage = true"
      @profile="router.push('/me')"
      @activities="router.push('/activities')"
    >
      <template #identity>
        <ZUserGroup v-for="group in user.groups" :key="group" :group="group" :grouping="false" />
        <ZUserPosition
          v-if="user.position.some((role) => role !== 'student') || !user.groups.length"
          :position="user.position"
        />
      </template>
      <template #footer>
        <UserHomeWorklist v-if="showWorklist" :is-admin="user.position.includes('admin')" />
      </template>
    </HomeOverview>
    <ElDialog v-model="reportPage" fullscreen destroy-on-close title="义工时长明细">
      <ZDetailedAnalysis
        v-if="reportPage"
        :key="user._id"
        :id="user._id"
        mode="on-campus"
        :sums="sums"
      />
    </ElDialog>
  </div>
</template>

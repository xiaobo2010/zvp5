<script setup lang="ts">
import type { Activity, ActivityMember } from '@/../types/v2'
import {
  ZActivityMemberList,
  ZActivityMember,
  ZActivityType,
  ZActivityStatus,
  ZActivityMode
} from '..'
import { computed, onMounted, ref, toRefs, withDefaults } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { ElPageHeader } from 'element-plus/es/components/page-header/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElButtonGroup } from 'element-plus/es/components/button/index.mjs'
import { ElDescriptions } from 'element-plus/es/components/descriptions/index.mjs'
import { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import { ElResult } from 'element-plus/es/components/result/index.mjs'
import { ArrowLeft, ArrowRight, Clock, Location, Plus, Timer, Edit } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { StreamlineUserEdit } from '@/icons'
import { useRouter } from 'vue-router'
import { watch } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from '@/plugins/dayjs'
import api from '@/api'
import { resolveErrorMessage } from '@/utils/errorMessage'
import { ZActivityDetails } from '.'
import { displayStatus } from '@/utils/activityStatus'

const user = useUserStore()
const router = useRouter()
const { width, height } = useWindowSize()
const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    id: string
    session: string
  }>(),
  {
    session: ''
  }
)

const { id, session } = toRefs(props)

const scroll = ref(height.value * 0.54 + 'px')
const loading = ref(false)

watch(
  () => height.value,
  (value) => {
    scroll.value = value * 0.54 + 'px'
  }
)

const mine = ref<ActivityMember>()
const activity = ref<Activity>()
const membersCount = ref(0)
const error = ref('')

const mineStatus = computed(() =>
  displayStatus({ status: activity.value?.status, mine: mine.value })
)

async function getInfo() {
  try {
    const memberTask = session.value
      ? api.activity.member.read(id.value, session.value).then((res) => {
          mine.value = res
        })
      : Promise.resolve()
    const activityTask = api.activity.read.single(id.value).then((response) => {
      activity.value = response.activity
      membersCount.value = response.members_count
    })
    await Promise.all([memberTask, activityTask])
    error.value = ''
  } catch (e) {
    // 失败必须有可见占位：否则 activity 为 undefined 时整页连 spinner 都不渲染（白页）。
    // 只有主数据缺失才算整页失败：成员信息读失败时就地降级，别把已经拿到的详情藏起来。
    if (!activity.value) {
      error.value = resolveErrorMessage(e, 'activity.view.loadFailed')
    }
  } finally {
    loading.value = false
  }
}

function retry() {
  loading.value = true
  void getInfo()
}

onMounted(() => {
  if (!user._id || !id.value) {
    loading.value = false
    error.value = t('activity.view.loadFailed')
    return
  }
  loading.value = true
  void getInfo()
})

const vert = ref(width.value < height.value * 1.2)

watch(width, () => {
  vert.value = width.value < height.value * 1.2
})

watch(height, () => {
  vert.value = width.value < height.value * 1.2
})
</script>

<template>
  <div v-loading="loading" class="yg-activity-page">
    <!-- 加载失败与「无数据」都要有可见占位，不能只靠 v-if="activity" 把整页渲染成空白 -->
    <ElResult v-if="error && !loading" icon="error" :title="error">
      <template #extra>
        <ElButton type="primary" text bg @click="retry">
          {{ t('activity.view.retry') }}
        </ElButton>
      </template>
    </ElResult>
    <template v-else-if="activity">
      <ElPageHeader v-if="activity?._id" :icon="ArrowLeft" @back="() => router.back()" class="py-4 yg-activity-page__header">
        <template #content>
          {{ activity.name }}
        </template>
      </ElPageHeader>
      <ZActivityDetails
        :activity="activity"
        mode="campus"
        :members-count="membersCount"
        show-members-inline
      />
      <ZActivityMemberList
        class="py-2"
        mode="inline"
        :activity="activity"
        :members-count="membersCount"
        @refresh="getInfo"
      />
      <!--<p class="text-gray-500 dark:text-gray-400 px-4 py-2" style="white-space: pre-wrap">
        {{ activity?.description }}
      </p>
      <ElDescriptions
        border
        class="py-2"
        :direction="vert ? 'vertical' : 'horizontal'"
        :column="vert ? 2 : undefined"
      >
        <ElDescriptionsItem :label="t('activity.form.type')">
          <ZActivityType
            force="full"
            :type="activity?.type"
            :bg="false"
          />
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('activity.registration.status.title')">
          <ZActivityStatus force="full" :type="activity?.status ?? 'effective'" :bg="false" />
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('activity.form.date')">
          <ElButton round size="small" :icon="Clock" text type="primary">{{
            dayjs(activity?.date).format('YYYY-MM-DD HH:mm')
          }}</ElButton>
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('activity.form.person')" v-if="activity">
          <ZActivityMemberList
            class="px-2"
            :activity="activity"
            :members-count="membersCount"
          />
        </ElDescriptionsItem>
      </ElDescriptions>-->
      <ElDescriptions
        v-if="mine"
        border
        class="py-2"
        :title="t('activity.view.panels.mine.name')"
        :direction="vert ? 'vertical' : 'horizontal'"
        :column="vert ? 2 : undefined"
      >
        <ElDescriptionsItem :label="t('activity.form.duration')">
          <ElButton
            :icon="Timer"
            type="info"
            text
            round
            size="small"
            :class="{ 'duration-pending': mineStatus === 'pending' }"
          >
            {{ mine?.duration ?? 0 }} h
          </ElButton>
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('activity.status.title')">
          <ZActivityStatus :type="mineStatus" size="small" force="full" />
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('activity.form.mode')">
          <ZActivityMode
            force="full"
            :mode="mine.mode"
            :bg="false"
            :show-my-duration="true"
            :show-properties="true"
          />
        </ElDescriptionsItem>
      </ElDescriptions>
      <div class="py-2 flex justify-end">
        <ZActivityMember
          :id="activity?.creator ?? ''"
          :icon="StreamlineUserEdit"
        />
        <ElButtonGroup class="px-2">
          <ElButton text bg round size="small" type="success" :icon="Plus">
            {{ dayjs(activity?.createdAt).format('YYYY-MM-DD HH:mm:ss') }}
          </ElButton>
          <ElButton text bg round size="small" type="warning" :icon="Edit">
            {{ dayjs(activity.updatedAt).format('YYYY-MM-DD HH:mm:ss') }}
          </ElButton>
        </ElButtonGroup>
      </div>
    </template>
  </div>
</template>

<style scoped>
.duration-pending.el-button {
  color: var(--el-text-color-secondary);
  text-decoration: line-through;
}

@media print {
  .no-print {
    display: none;
  }
}
</style>

<style>
@media print {
  body {
    margin: 0;
    padding: 0;
    filter: grayscale(100%);
  }
}
</style>

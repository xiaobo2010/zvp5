<script setup lang="ts">
import { useUserStore } from '@/stores/user'
import { ElButton, ElIcon } from 'element-plus'
import { ArrowRight, InfoFilled, List, Refresh, User } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { computed, ref, watch } from 'vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'
import ZUserPosition from '@/components/tags/ZUserPosition.vue'
import ZUserTimeJudge from '@/components/activity/ZUserTimeJudge.vue'
import UserHomeWorklist from '@/components/user/UserHomeWorklist.vue'

const header = useHeaderStore()
const user = useUserStore()
const { t } = useI18n()
const router = useRouter()

if (!user.isLogin) {
  router.replace('/user/login')
}

header.setHeader(t('nav.home'))

const loading = ref(false)
const nowTime = dayjs().hour()
const greeting = ref(nowTime < 12 ? 'morning' : nowTime < 18 ? 'afternoon' : 'evening')
const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
const dateText = `${dayjs().format('M月D日')} ${weekdays[dayjs().day()]}`
const initial = computed(() => (user.name || '?').slice(0, 1))
const showWorklist = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)

async function refreshUser() {
  loading.value = true
  try {
    await user.refreshUser()
  } finally {
    loading.value = false
  }
}

watch(
  () => user.isLogin,
  (value) => {
    if (!value) router.replace('/user/login')
  }
)
</script>

<template>
  <div v-if="user.isLogin" class="yg-v2-page yg-home-page">
    <header class="yg-page-header yg-home-header">
      <div>
        <p class="yg-home-date">{{ dateText }}</p>
        <h1 class="yg-page-header__title">
          {{ t('home.greeting', { greet: t('home.greetings.' + greeting), name: user.name }) }}
        </h1>
        <p class="yg-page-header__subtitle">把每一次参与，都记录成看得见的成长。</p>
      </div>
      <ElButton
        :icon="Refresh"
        :loading="loading"
        circle
        aria-label="刷新个人资料"
        @click="refreshUser"
      />
    </header>

    <section class="yg-card" aria-labelledby="home-identity-title">
      <div class="yg-card__body yg-home-identity">
        <span class="yg-home-avatar" aria-hidden="true">{{ initial }}</span>
        <div class="yg-home-identity__body">
          <h2 id="home-identity-title" class="yg-home-identity__name">
            {{ user.name }}
          </h2>
          <p class="yg-home-identity__meta">{{ t('home.labels.number') }} {{ user.id }}</p>
          <div class="yg-home-identity__roles">
            <ZUserGroup
              v-for="group in user.groups"
              :key="group"
              :group="group"
              :grouping="false"
            />
            <ZUserPosition
              v-if="user.position.find((role) => role !== 'student') || !user.groups.length"
              :position="user.position"
            />
          </div>
        </div>
        <RouterLink class="yg-btn yg-btn--secondary yg-home-identity__action" to="/me">
          编辑资料
        </RouterLink>
      </div>
    </section>

    <ZUserTimeJudge
      :user="user._id"
      :on-campus="user.time.onCampus"
      :off-campus="user.time.offCampus"
      :social-practice="user.time.socialPractice"
      discount
    />

    <section class="yg-card" aria-labelledby="home-actions-title">
      <div class="yg-card__header">
        <h2 id="home-actions-title" class="yg-card__title">快捷入口</h2>
      </div>
      <div class="yg-card__body">
        <div class="yg-home-actions">
          <RouterLink class="yg-card yg-card--hoverable yg-home-action" to="/activities/mine">
            <span class="yg-home-action__icon" aria-hidden="true"
              ><ElIcon><List /></ElIcon
            ></span>
            <span class="yg-home-action__text">
              <b>我的义工</b>
              <small>查看参与记录与活动状态</small>
            </span>
            <ElIcon class="yg-home-action__arrow" aria-hidden="true"><ArrowRight /></ElIcon>
          </RouterLink>
          <RouterLink class="yg-card yg-card--hoverable yg-home-action" to="/me">
            <span class="yg-home-action__icon" aria-hidden="true"
              ><ElIcon><User /></ElIcon
            ></span>
            <span class="yg-home-action__text">
              <b>个人资料</b>
              <small>学号、班级与设备状态</small>
            </span>
            <ElIcon class="yg-home-action__arrow" aria-hidden="true"><ArrowRight /></ElIcon>
          </RouterLink>
          <RouterLink class="yg-card yg-card--hoverable yg-home-action" to="/about">
            <span class="yg-home-action__icon" aria-hidden="true"
              ><ElIcon><InfoFilled /></ElIcon
            ></span>
            <span class="yg-home-action__text">
              <b>关于平台</b>
              <small>版本信息与开源许可</small>
            </span>
            <ElIcon class="yg-home-action__arrow" aria-hidden="true"><ArrowRight /></ElIcon>
          </RouterLink>
        </div>
      </div>
    </section>

    <UserHomeWorklist v-if="showWorklist" :is-admin="user.position.includes('admin')" />
  </div>
</template>

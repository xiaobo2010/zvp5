<script setup lang="ts">
import { useUserStore } from '@/stores/user'
import dayjs from '@/plugins/dayjs'
import { computed, ref, watch } from 'vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'
import ZUserPosition from '@/components/tags/ZUserPosition.vue'
import UserHomeWorklist from '@/components/user/UserHomeWorklist.vue'
import UserTimePanel from './UserTimePanel.vue'

const header = useHeaderStore()
const user = useUserStore()
const { t } = useI18n()
const router = useRouter()

if (!user.isLogin) {
  router.replace('/user/login')
}

header.setHeader(t('nav.home'))

const nowTime = dayjs().hour()
const greeting = ref(nowTime < 12 ? 'morning' : nowTime < 18 ? 'afternoon' : 'evening')
const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
const dateText = `${dayjs().format('M月D日')} ${weekdays[dayjs().day()]}`
const stamp = dayjs().format('YYYY.MM.DD')
const initial = computed(() => (user.name || '?').slice(0, 1))
const totalHours = computed(() => {
  const time = user.time
  return (Number(time.onCampus || 0) + Number(time.offCampus || 0) + Number(time.socialPractice || 0))
    .toFixed(1)
    .replace(/\.0$/, '')
})
const showWorklist = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)

watch(
  () => user.isLogin,
  (value) => {
    if (!value) router.replace('/user/login')
  }
)
</script>

<template>
  <div v-if="user.isLogin" class="yg-v2-page yg-home-page">
    <section class="yg-phi-hero" aria-labelledby="phi-hero-title">
      <div class="yg-phi-hero__rail" aria-hidden="true">
        <span>ZVMS // VOLUNTEER</span>
        <span>{{ stamp }}</span>
      </div>
      <h1 id="phi-hero-title" class="yg-phi-title" data-text="ZVMS">ZVMS</h1>
      <p class="yg-phi-subtitle">VOLUNTEER <b>SYSTEM</b></p>
      <p class="yg-phi-meta">
        {{ t('home.greeting', { greet: t('home.greetings.' + greeting), name: user.name }) }}
        <span aria-hidden="true">/</span>
        {{ dateText }}
      </p>
      <p class="yg-phi-welcome">把每一次参与，都记录成看得见的成长。</p>
      <div class="yg-phi-emblem" aria-hidden="true" title="义工总时长">
        <span>{{ totalHours }}<small>H</small></span>
      </div>
    </section>

    <section class="yg-card yg-phi-identity" aria-labelledby="home-identity-title">
      <div class="yg-card__body yg-home-identity">
        <span class="yg-home-avatar" aria-hidden="true">{{ initial }}</span>
        <div class="yg-home-identity__body">
          <h2 id="home-identity-title" class="yg-home-identity__name">
            {{ user.name }}
          </h2>
          <p class="yg-home-identity__meta">
            {{ t('home.labels.number') }} <b>{{ user.id }}</b>
          </p>
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

    <UserTimePanel
      :user="user._id"
      :on-campus="user.time.onCampus"
      :off-campus="user.time.offCampus"
      :social-practice="user.time.socialPractice"
      discount
    />

    <UserHomeWorklist v-if="showWorklist" :is-admin="user.position.includes('admin')" />
  </div>
</template>

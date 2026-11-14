<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useHeaderStore } from '@/stores/header'
import { useUserStore } from '@/stores/user'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'
import ZUserPosition from '@/components/tags/ZUserPosition.vue'
import ZUserTimeRings from '@/components/activity/ZUserTimeRings.vue'

const header = useHeaderStore()
const user = useUserStore()
const router = useRouter()
const { t } = useI18n()

if (!user.isLogin) {
  router.replace('/user/login')
}

header.setHeader(t('nav.home'))

const loading = ref(false)

const greeting = computed(() => {
  const hour = new Date().getHours()
  return hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening'
})

const greetingText = computed(() =>
  t('home.greeting', { greet: t('home.greetings.' + greeting.value), name: user.name })
)

const today = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long'
}).format(new Date())

const isSecretary = computed(() => user.position.includes('secretary'))

const time = reactive({
  socialPractice: user.time.socialPractice,
  onCampus: user.time.onCampus,
  offCampus: user.time.offCampus
})

async function refreshUser() {
  if (loading.value) {
    return
  }
  loading.value = true
  await user.refreshUser()
  time.socialPractice = user.time.socialPractice
  time.onCampus = user.time.onCampus
  time.offCampus = user.time.offCampus
  loading.value = false
}

watch(
  () => user.isLogin,
  (v) => {
    if (!v) {
      router.replace('/user/login')
    }
  }
)
</script>

<template>
  <div v-if="user.isLogin" class="yg2-page">
    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">{{ today }}</p>
        <h1 class="yg2-page-header__title">{{ greetingText }}</h1>
      </div>
      <div class="yg2-page-header__actions">
        <button
          class="yg2-btn yg2-btn--secondary yg2-btn--sm"
          type="button"
          :disabled="loading"
          @click="refreshUser"
        >
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
        <button
          v-if="isSecretary"
          class="yg2-btn yg2-btn--primary yg2-btn--sm"
          type="button"
          @click="router.push('/group/' + user.class_id)"
        >
          管理班级
        </button>
      </div>
    </header>

    <div class="yg2-home">
      <section class="yg2-card yg2-home__card yg2-home__card--profile" :class="{ 'is-loading': loading }">
        <h2 class="yg2-card__title">{{ t('home.panels.information.title') }}</h2>
        <dl class="yg2-kv">
          <dt>{{ t('home.labels.name') }}</dt>
          <dd>{{ user.name }}</dd>

          <dt>{{ t('home.labels.number') }}</dt>
          <dd class="yg2-num">{{ user.id }}</dd>

          <dt>{{ t('home.labels.identify') }}</dt>
          <dd class="yg2-home__tags">
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
          </dd>
        </dl>
      </section>

      <section class="yg2-card yg2-home__card yg2-home__card--links">
        <h2 class="yg2-card__title">快捷入口</h2>
        <nav class="yg2-shortcuts">
          <RouterLink class="yg2-shortcut" to="/activities/mine">
            <span class="yg2-shortcut__main">浏览义工列表</span>
            <span class="yg2-shortcut__meta">看看最近能报什么</span>
            <span class="yg2-shortcut__arrow" aria-hidden="true">›</span>
          </RouterLink>
          <RouterLink
            v-if="user.class_id"
            class="yg2-shortcut"
            :to="'/group/' + user.class_id"
          >
            <span class="yg2-shortcut__main">我的小组</span>
            <span class="yg2-shortcut__meta">班级成员与时长</span>
            <span class="yg2-shortcut__arrow" aria-hidden="true">›</span>
          </RouterLink>
          <RouterLink class="yg2-shortcut" to="/ai/chat">
            <span class="yg2-shortcut__main">AI 助手</span>
            <span class="yg2-shortcut__meta">问问活动怎么选</span>
            <span class="yg2-shortcut__arrow" aria-hidden="true">›</span>
          </RouterLink>
        </nav>
      </section>

      <section
        class="yg2-card yg2-home__card yg2-home__card--time"
        :class="{ 'is-blurred': user.shouldResetPassword }"
      >
        <ZUserTimeRings :user="user._id" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.yg2-home {
  display: grid;
  grid-template-columns: minmax(0, 340px) minmax(0, 1fr);
  grid-template-areas:
    'profile time'
    'links time';
  gap: 16px;
  align-items: start;
}

.yg2-home__card--profile {
  grid-area: profile;
}

.yg2-home__card--links {
  grid-area: links;
}

.yg2-home__card--time {
  grid-area: time;
}

.yg2-home__card {
  padding: 24px;
}

.yg2-home__card.is-loading {
  opacity: 0.55;
}

.yg2-home__tags {
  flex-wrap: wrap;
}

/* 快捷入口 */
.yg2-shortcuts {
  display: grid;
  gap: 2px;
}

.yg2-shortcut {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    'main arrow'
    'meta arrow';
  gap: 2px 10px;
  padding: 10px 12px;
  border-radius: var(--yg2-r-12);
  color: inherit;
  text-decoration: none;
  transition: background var(--yg2-dur-1) var(--yg2-ease-out);
}

.yg2-shortcut:hover {
  background: var(--yg2-surface-sunken);
}

.yg2-shortcut:focus-visible {
  outline: 3px solid var(--yg2-focus);
  outline-offset: 1px;
}

.yg2-shortcut__main {
  grid-area: main;
  font-size: 15px;
  font-weight: 500;
  color: var(--yg2-text);
}

.yg2-shortcut__meta {
  grid-area: meta;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.yg2-shortcut__arrow {
  grid-area: arrow;
  align-self: center;
  font-size: 20px;
  line-height: 1;
  color: var(--yg2-text-tertiary);
}

/* 时长组件自带 ElCard：去掉内层描边/底色，与页面卡片融为一体 */
.yg2-home__card--time :deep(.el-card) {
  border: 0;
  background: transparent;
  box-shadow: none;
}

.yg2-home__card--time :deep(.el-card__body) {
  padding: 0;
}

.yg2-home__card--time :deep(.text-lg) {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

/* 强制改密前先挡住时长明细，避免绕过 */
.yg2-home__card--time.is-blurred {
  filter: blur(18px);
  pointer-events: none;
  user-select: none;
}

@media (max-width: 1024px) {
  .yg2-home {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'profile'
      'time'
      'links';
  }
}

@media (max-width: 768px) {
  .yg2-home__card {
    padding: 18px;
  }
}
</style>

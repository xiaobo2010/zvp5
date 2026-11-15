<script setup lang="ts">
import type { Activity, ActivityMember } from '@/../types/v2'
import { ZActivityMemberList, ZActivityStatus, ZActivityType, ZActivityDuration } from '..'
import { computed, onMounted, ref, toRefs, withDefaults } from 'vue'
import StateBlock from '@/ui/ux/StateBlock.vue'
import { useUserStore } from '@/stores/user'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import dayjs from '@/plugins/dayjs'
import api from '@/api'
import { resolveErrorMessage } from '@/utils/errorMessage'
import { ZActivityDetails } from '.'
import { displayStatus } from '@/utils/activityStatus'

const user = useUserStore()
const router = useRouter()
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

const loading = ref(false)
const mine = ref<ActivityMember>()
const activity = ref<Activity>()
const membersCount = ref(0)
const error = ref('')

const mineStatus = computed(() =>
  displayStatus({ status: activity.value?.status, mine: mine.value })
)

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function weekday(date: string | undefined) {
  return date ? (WEEKDAYS[new Date(date).getDay()] ?? '') : ''
}

async function getInfo() {
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
  loading.value = false
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
</script>

<template>
  <div v-if="error && !loading" class="yg2-page yg2-detail">
    <StateBlock tone="error" :title="error">
      <template #actions>
        <button class="yg2-btn yg2-btn--primary" type="button" @click="retry">
          {{ t('activity.view.retry') }}
        </button>
      </template>
    </StateBlock>
  </div>
  <div v-else-if="activity" v-loading="loading" class="yg2-page yg2-detail">
    <button class="yg2-detail__back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-detail__hero">
      <div class="yg2-detail__date">
        <span class="yg2-detail__day">{{ dayjs(activity.date).format('DD') }}</span>
        <span class="yg2-detail__month">{{ dayjs(activity.date).format('M月') }}</span>
        <span class="yg2-detail__week">{{ weekday(activity.date) }}</span>
      </div>

      <div class="yg2-detail__headline">
        <p class="yg2-page-header__eyebrow">
          {{ t('activity.mode.' + activity.type + '.name', activity.type) }}
        </p>
        <h1 class="yg2-detail__title">{{ activity.name }}</h1>
        <p class="yg2-detail__meta">
          <span>{{ dayjs(activity.date).format('M月D日 HH:mm') }}</span>
          <template v-if="activity.place">
            <span class="yg2-detail__dot" aria-hidden="true">·</span>
            <span>{{ activity.place }}</span>
          </template>
        </p>
      </div>

      <div class="yg2-detail__badges">
        <ZActivityStatus v-if="mine" :type="mineStatus" force="full" />
        <ZActivityType v-else :type="activity.type" force="full" :status="activity.status" />
      </div>
    </header>

    <div class="yg2-detail__body">
      <ZActivityDetails
        :activity="activity"
        mode="campus"
        :members-count="membersCount"
        show-members-inline
      />

      <aside class="yg2-detail__aside">
        <section v-if="mine" class="yg2-card yg2-detail__card">
          <h2 class="yg2-card__title">{{ t('activity.view.panels.mine.name') }}</h2>
          <dl class="yg2-kv">
            <dt>{{ t('activity.form.duration') }}</dt>
            <dd>
              <ZActivityDuration
                :mode="mine.mode"
                :duration="mine.duration"
                force="full"
              />
            </dd>
            <dt>{{ t('activity.status.title') }}</dt>
            <dd><ZActivityStatus :type="mineStatus" size="small" force="full" /></dd>
          </dl>
        </section>

        <section class="yg2-card yg2-detail__card">
          <h2 class="yg2-card__title">{{ t('activity.form.person') }}</h2>
          <ZActivityMemberList
            mode="inline"
            :activity="activity"
            :members-count="membersCount"
            @refresh="getInfo"
          />
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.yg2-detail__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  margin-bottom: var(--yg2-sp-4);
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--yg2-dur-1) var(--yg2-ease-out),
    color var(--yg2-dur-1) var(--yg2-ease-out);
}

.yg2-detail__back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.yg2-detail__back:focus-visible {
  outline: 3px solid var(--yg2-focus);
  outline-offset: 2px;
}

/* 抬头：票根日期 + 标题 + 状态 */
.yg2-detail__hero {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--yg2-sp-6);
  padding: 0 0 var(--yg2-sp-6);
  border-bottom: 1px dashed var(--yg2-line-strong);
  margin-bottom: var(--yg2-sp-6);
}

.yg2-detail__date {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 2px;
  min-width: 92px;
  padding: 10px 16px;
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-20);
  background: var(--yg2-surface);
  box-shadow: var(--yg2-shadow-ambient);
}

.yg2-detail__day {
  font-size: 34px;
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 1.05;
  font-variant-numeric: tabular-nums;
  color: var(--yg2-text);
}

.yg2-detail__month {
  font-size: 12px;
  font-weight: 500;
  color: var(--yg2-text-secondary);
}

.yg2-detail__week {
  font-size: 11px;
  color: var(--yg2-text-tertiary);
}

.yg2-detail__title {
  margin: 0;
  font-size: 32px;
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1.15;
  color: var(--yg2-text);
}

.yg2-detail__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0;
  font-size: 14px;
  color: var(--yg2-text-secondary);
}

.yg2-detail__dot {
  color: var(--yg2-text-tertiary);
}

.yg2-detail__badges {
  display: flex;
  align-items: center;
  gap: 8px;
}

.yg2-detail__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.yg2-detail__aside {
  display: grid;
  gap: 16px;
}

.yg2-detail__card {
  padding: 20px;
}

@media (max-width: 1024px) {
  .yg2-detail__body {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 768px) {
  .yg2-detail__hero {
    grid-template-columns: auto minmax(0, 1fr);
    gap: var(--yg2-sp-4);
  }

  .yg2-detail__badges {
    grid-column: 1 / -1;
  }

  .yg2-detail__title {
    font-size: 24px;
  }

  .yg2-detail__date {
    min-width: 76px;
    padding: 8px 12px;
  }

  .yg2-detail__day {
    font-size: 28px;
  }
}
</style>

<script setup lang="ts">
import StateBlock from '@/ui/ux/StateBlock.vue'
import { usePullToRefresh } from '@/ui/ux/usePullToRefresh'
import type { Activity, ActivityMember } from '@/../types/v2'
import { ElDrawer } from 'element-plus'
import { onMounted, ref, toRefs, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import dayjs from '@/plugins/dayjs'
import { useI18n } from 'vue-i18n'
import { getActivity } from './getActivity'
import { ZActivityType, ZActivityStatus, ZActivityDuration } from '@/components'
import { displayStatus } from '@/utils/activityStatus'
import { useRouter, useRoute } from 'vue-router'
import ZDataExport from '@/components/utils/ZDataExport.vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import { pad } from '@/plugins/ua.ts'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const user = useUserStore()

const props = withDefaults(
  defineProps<{
    role: 'campus' | 'class' | 'mine'
    embed?: boolean
    perspective?: string
    classTarget?: string
  }>(),
  {
    role: 'mine',
    embed: false,
    perspective: 'mine'
  }
)

const { role, perspective: persp, classTarget, embed } = toRefs(props)

const activePage = ref(parseInt(route.query?.page?.toString() ?? '1') ?? 1)
const pageSize = ref(parseInt(route.query?.perpage?.toString() ?? '12') ?? 12)
const size = ref(0)

// eslint-disable-next-line vue/no-dupe-keys
const perspective = ref(persp.value === 'mine' ? user._id : persp.value)
const loading = ref(true)
const loadFailed = ref(false)
const searchWord = ref(route.query?.search?.toString() ?? '')
const query = ref(route.query?.search?.toString() ?? '')

type ActivityRow = Activity & { mine?: ActivityMember }

const activities = ref<ActivityRow[]>([])
const sort = ref('_id')
const asc = ref(false)

function refresh() {
  loading.value = true
  loadFailed.value = false
  getActivity(
    perspective.value === 'mine' ? user._id : perspective.value,
    role.value,
    activePage.value,
    pageSize.value,
    query.value,
    classTarget.value ?? user.class_id ?? '',
    '',
    sort.value,
    asc.value
  )
    .then((res) => {
      activities.value = res?.activities ?? []
      size.value = res?.total ?? 0
      loading.value = false
    })
    .catch(() => {
      activities.value = []
      size.value = 0
      loadFailed.value = true
      loading.value = false
    })
}

// 触屏下拉刷新（只在列表滚动到顶时接管手势）
const { pulling, refreshing } = usePullToRefresh(() => {
  refresh()
})

onMounted(refresh)

/** 空态里的「浏览全部项目」：切到全校视角的列表页。 */
function openAll() {
  router.push('/activities/campus')
}

function confirmPage() {
  refresh()
  router.replace({
    query: { perpage: pageSize.value, page: activePage.value, search: searchWord.value }
  })
}

watch(pageSize, confirmPage)
watch(activePage, confirmPage)
watch(query, confirmPage)

const openExport = ref(false)

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

function weekday(date: string) {
  return WEEKDAYS[new Date(date).getDay()] ?? ''
}

function open(row: ActivityRow) {
  const session = row.mine?._id ? '?session=' + row.mine._id : ''
  router.push(`/activity/details/${row._id}${session}`)
}

function typeLabel(type: string) {
  return t('activity.mode.' + type + '.name', type)
}
</script>

<template>
  <div class="yg2-activity" :class="{ 'is-embed': embed }">
    <ElDrawer
      direction="rtl"
      size="50%"
      v-model="openExport"
      :title="t('manage.exports.title')"
      center
    >
      <ZDataExport type="time" v-model="openExport" />
    </ElDrawer>

    <header v-if="!embed" class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">{{ t('nav.activity') }}</p>
        <h1 class="yg2-page-header__title">
          <slot name="title">{{ t('nav.activity') }}</slot>
        </h1>
        <p class="yg2-page-header__subtitle">共 {{ size }} 个项目</p>
      </div>
      <div class="yg2-page-header__actions">
        <button
          class="yg2-btn yg2-btn--secondary yg2-btn--sm"
          type="button"
          :disabled="loading"
          @click="refresh"
        >
          {{ t('activity.form.actions.refresh') }}
        </button>
        <button
          v-if="role === 'campus' && !pad()"
          class="yg2-btn yg2-btn--secondary yg2-btn--sm"
          type="button"
          :disabled="!user.position.includes('admin')"
          @click="openExport = true"
        >
          {{ t('activity.export.name') }}
        </button>
      </div>
    </header>

    <div class="yg2-activity__bar">
      <input
        v-model="searchWord"
        class="yg2-input yg2-activity__search"
        type="search"
        :placeholder="t('activity.form.searchPlaceholder', '搜索项目名称')"
        @keydown.enter="query = searchWord"
        @blur="query = searchWord"
      />
      <button
        v-if="embed"
        class="yg2-btn yg2-btn--secondary yg2-btn--sm"
        type="button"
        :disabled="loading"
        @click="refresh"
      >
        {{ t('activity.form.actions.refresh') }}
      </button>
    </div>

    <div
      v-if="pulling || refreshing"
      class="yg2-ptr__hint"
      :class="{ 'is-pulling': pulling || refreshing }"
    >
      <span v-if="refreshing" class="yg2-spinner" />
      <span v-else>松手刷新</span>
    </div>

    <div v-if="loading" class="yg2-tickets" aria-busy="true">
      <div v-for="n in 6" :key="n" class="yg2-ticket yg2-ticket--skeleton">
        <span class="yg2-skeleton yg2-ticket__sk-date" />
        <span class="yg2-ticket__sk-main">
          <span class="yg2-skeleton" style="width: 42%; height: 12px" />
          <span class="yg2-skeleton" style="width: 72%; height: 18px" />
          <span class="yg2-skeleton" style="width: 30%; height: 12px" />
        </span>
      </div>
    </div>

    <StateBlock
      v-else-if="loadFailed"
      tone="error"
      title="没能加载项目列表"
      description="网络或服务端出了问题，不是你的操作有误。稍后重试通常就能恢复。"
    >
      <template #actions>
        <button class="yg2-btn yg2-btn--primary" type="button" @click="refresh">重新试试</button>
      </template>
    </StateBlock>

    <div v-else-if="activities.length" class="yg2-tickets">
      <button
        v-for="row in activities"
        :key="row._id"
        class="yg2-ticket"
        type="button"
        @click="open(row)"
      >
        <span class="yg2-ticket__date">
          <span class="yg2-ticket__day">{{ dayjs(row.date).format('DD') }}</span>
          <span class="yg2-ticket__month">{{ dayjs(row.date).format('M月') }}</span>
          <span class="yg2-ticket__week">{{ weekday(row.date) }}</span>
        </span>

        <span class="yg2-ticket__main">
          <span class="yg2-ticket__kicker">
            <ZActivityType :type="row.type" size="small" special="other" />
            <span v-if="row.place" class="yg2-ticket__place">{{ row.place }}</span>
          </span>
          <span class="yg2-ticket__title">{{ row.name }}</span>
          <span class="yg2-ticket__meta">
            <ZActivityStatus
              v-if="role === 'mine'"
              :type="displayStatus(row)"
              size="small"
              force="full"
            />
            <span v-else class="yg2-badge">{{ typeLabel(row.type) }}</span>
            <ZActivityDuration
              v-if="row.mine"
              :mode="row.mine.mode"
              :duration="row.mine.duration"
              force="short"
            />
          </span>
        </span>

        <span class="yg2-ticket__chevron" aria-hidden="true">›</span>
      </button>
    </div>

    <StateBlock
      v-else
      title="没有找到项目"
      description="换个关键词，或稍后再来看看；也可以先浏览全部项目。"
    >
      <template #actions>
        <button
          v-if="query"
          class="yg2-btn yg2-btn--secondary"
          type="button"
          @click="searchWord = ''; query = ''; refresh()"
        >
          清空关键词
        </button>
        <button class="yg2-btn yg2-btn--primary" type="button" @click="openAll">
          浏览全部项目
        </button>
      </template>
    </StateBlock>

    <div v-if="activities.length" class="yg2-activity__pager">
      <ZPagination v-model:current-page="activePage" v-model:page-size="pageSize" :total="size" />
    </div>
  </div>
</template>

<style scoped>
.yg2-activity {
  width: 100%;
  max-width: var(--yg2-content-max);
  margin: 0 auto;
  padding: var(--yg2-sp-8) var(--yg2-gutter) var(--yg2-sp-12);
}

.yg2-activity.is-embed {
  padding: 0;
}

.yg2-activity__bar {
  display: flex;
  gap: 10px;
  margin-bottom: var(--yg2-sp-4);
}

.yg2-activity__search {
  max-width: 320px;
  height: 40px;
  font-size: 14px;
}

.yg2-tickets {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

/* 票券卡：日期铅字 + 虚线撕口 + 右侧箭头 */
.yg2-ticket {
  --ticket-divider: 92px;

  position: relative;
  display: grid;
  grid-template-columns: var(--ticket-divider) minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 112px;
  padding-right: 16px;
  border: 0;
  border-radius: var(--yg2-r-20);
  /* 列表项不做玻璃（一屏十几块会打穿 GPU），但材质语言要和卡片一致：
     顶光 + 发丝内边 + 分层投影，实色面板档。 */
  background: var(--yg2-lens-panel-fill);
  box-shadow:
    inset 0 1px 0 var(--yg2-lens-matte-bloom),
    inset 0 0 0 1px var(--yg2-lens-matte-hairline),
    var(--yg2-lens-matte-cast);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: transform var(--yg2-dur-2) var(--yg2-ease-out),
    border-color var(--yg2-dur-2) var(--yg2-ease-out),
    box-shadow var(--yg2-dur-2) var(--yg2-ease-out);
}

.yg2-ticket:hover {
  transform: translateY(-2px);
  box-shadow:
    inset 0 1px 0 var(--yg2-lens-matte-bloom),
    inset 0 0 0 1px color-mix(in srgb, var(--yg2-accent) 26%, transparent),
    0 2px 4px rgba(15, 23, 42, 0.06),
    0 18px 40px -20px rgba(15, 23, 42, 0.26);
}

.yg2-ticket:focus-visible {
  outline: 3px solid var(--yg2-focus);
  outline-offset: 2px;
}

/* 撕口：卡片上下沿各挖一个半圆 */
.yg2-ticket::before,
.yg2-ticket::after {
  content: '';
  position: absolute;
  left: var(--ticket-divider);
  width: 12px;
  height: 12px;
  margin-left: -6px;
  border: 1px solid var(--yg2-line);
  border-radius: 50%;
  background: var(--yg2-bg);
}

.yg2-ticket::before {
  top: -7px;
  clip-path: inset(0 0 50% 0);
}

.yg2-ticket::after {
  bottom: -7px;
  clip-path: inset(50% 0 0 0);
}

.yg2-ticket__date {
  position: relative;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 1px;
  align-self: stretch;
  padding: 16px 0;
}

/* 撕线：绝对定位，保证与卡片同高 */
.yg2-ticket__date::after {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  right: -1px;
  border-right: 1px dashed var(--yg2-line-strong);
}

.yg2-ticket__day {
  font-size: 27px;
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 1.05;
  color: var(--yg2-text);
  font-variant-numeric: tabular-nums;
}

.yg2-ticket__month {
  font-size: 11px;
  font-weight: 500;
  color: var(--yg2-text-tertiary);
}

.yg2-ticket__week {
  font-size: 11px;
  color: var(--yg2-text-tertiary);
}

.yg2-ticket__main {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.yg2-ticket__kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.yg2-ticket__place {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.yg2-ticket__title {
  overflow: hidden;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--yg2-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.yg2-ticket__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--yg2-text-secondary);
}

.yg2-ticket__chevron {
  font-size: 20px;
  line-height: 1;
  color: var(--yg2-text-tertiary);
  transition: transform var(--yg2-dur-1) var(--yg2-ease-out);
}

.yg2-ticket:hover .yg2-ticket__chevron {
  transform: translateX(2px);
  color: var(--yg2-accent);
}

.yg2-ticket__sk-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: center;
}

.yg2-ticket__sk-date {
  width: 44px;
  height: 44px;
  border-radius: var(--yg2-r-12);
}

.yg2-ticket--skeleton {
  height: 112px;
  border-style: dashed;
  cursor: default;
  background: transparent;
  box-shadow: none;
  animation: yg2-ticket-pulse 1.4s ease-in-out infinite;
}

@keyframes yg2-ticket-pulse {
  50% {
    opacity: 0.45;
  }
}

.yg2-activity__pager {
  display: flex;
  justify-content: center;
  padding-top: var(--yg2-sp-6);
}

.yg2-empty__hint {
  margin: 0;
  font-size: 13px;
  color: var(--yg2-text-tertiary);
}

@media (max-width: 768px) {
  .yg2-activity {
    padding: var(--yg2-sp-6) var(--yg2-sp-4) var(--yg2-sp-10);
  }

  .yg2-tickets {
    grid-template-columns: minmax(0, 1fr);
  }

  .yg2-activity__search {
    max-width: none;
  }
}
</style>

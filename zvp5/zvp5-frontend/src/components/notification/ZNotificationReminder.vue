<script setup lang="ts">
/**
 * 站内通知的顶部横幅。
 *
 * 以前这里是全屏模态弹窗（ElDialog），登录后一出现就挡住整页、还要排队点掉。
 * 现在改成页面顶部的非模态消息框：
 *   - 不遮罩、不锁滚动、不抢焦点（没有 autofocus，也没有焦点陷阱）；
 *   - Esc 可以收起；
 *   - 内容区可滚动，操作按钮始终可点。
 *
 * 队列：服务端返回的未读通知逐条展示，右上角给出「第 N / M 条」。
 * 「已读」写穿服务端（乐观更新 + 失败回滚），「不再显示」只落本地。
 */
import {
  computed,
  defineAsyncComponent,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch
} from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { formatDateTime } from '@/utils/dateTime'
import { notificationAccountKey } from '@/utils/notificationState'
import {
  bindNotificationAccount,
  isUnreadNotification,
  markNotificationRead,
  notificationRuntime,
  suppressNotificationLocally,
  syncNotifications
} from './notificationStore'
import type { NotificationRecord } from '@/api/notifications'

// 通知正文的 markdown 渲染(markdown-it + dompurify)不在首屏路径上：
// 横幅出现时才加载，避免这两个库随 App 壳进入入口 chunk。
const ZMarkdown = defineAsyncComponent(() => import('@/components/markdown/ZMarkdown.vue'))

/** 登录后先给页面 10s 稳定期再取通知列表，避免刚进入就打断主流程。 */
const INITIAL_DELAY_MS = 10_000
/** 两次主动检查之间的最小间隔：路由切换 / 可见性变化都走这个节流。 */
const MIN_CHECK_INTERVAL_MS = 60_000

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/**
 * 账号唯一 id 优先使用 oid。切换账号时 watcher 会立即丢弃旧队列、旧响应和
 * 旧本地状态；key 本身也按账号隔离，绝不会把上一个账号的通知带给下一个人。
 */
const accountKey = computed(() => {
  if (!userStore.isLogin) {
    return ''
  }
  return notificationAccountKey(userStore._id || userStore.id)
})

/** 未读队列：服务端顺序（最新在前），逐条弹出。 */
const queue = computed<NotificationRecord[]>(() =>
  notificationRuntime.notifications.filter((notice) => isUnreadNotification(notice))
)
const current = computed<NotificationRecord | null>(() =>
  dismissed.value ? null : (queue.value[0] ?? null)
)
const total = computed(() => queue.value.length)

const advancing = ref(false)
/** 最近一次「已读」写失败：横幅给出轻量重试入口，而不是静默吞错。 */
const readError = ref(false)
/** 用户主动收起横幅：本次会话内不再自动弹出（下次检查仍然会重新评估）。 */
const dismissed = ref(false)

let generation = 0
let inFlight = false
let lastCheckedAt = 0
let readyAt = 0
let initialTimer: ReturnType<typeof setTimeout> | null = null

function resetForAccount() {
  generation += 1
  readError.value = false
  dismissed.value = false
  advancing.value = false
  lastCheckedAt = 0
  readyAt = 0
  if (initialTimer) {
    clearTimeout(initialTimer)
    initialTimer = null
  }
}

/**
 * 检查通知。登录后的 10s 稳定期内不请求；同一时刻只允许一个请求；
 * 两次检查之间至少隔 60s（路由切换 / 可见性变化都走这个节流）。
 */
async function checkNotifications(force = false) {
  if (!force && readyAt > 0 && Date.now() < readyAt) {
    return
  }
  if (!accountKey.value || !userStore.isLogin) {
    return
  }
  if (inFlight) {
    return
  }
  const now = Date.now()
  if (!force && lastCheckedAt > 0 && now - lastCheckedAt < MIN_CHECK_INTERVAL_MS) {
    return
  }
  const requestGeneration = generation
  inFlight = true
  lastCheckedAt = now
  try {
    await syncNotifications()
    if (requestGeneration !== generation) {
      return
    }
  } finally {
    inFlight = false
  }
}

/** 「已读」：乐观更新 → 写穿服务端；失败则回滚，横幅给出重试入口。 */
async function acknowledge() {
  const notice = current.value
  if (!notice || advancing.value) {
    return
  }
  advancing.value = true
  readError.value = false
  try {
    const ok = await markNotificationRead(notice)
    readError.value = !ok
  } finally {
    advancing.value = false
  }
}

/** 「不再显示」：只落本地（本机当前账号永久隐藏），不上云。 */
function suppress() {
  const notice = current.value
  if (!notice || advancing.value) {
    return
  }
  advancing.value = true
  readError.value = false
  suppressNotificationLocally(notice)
  advancing.value = false
}

/** Esc / × 收起：横幅退场，未读通知留待下次检查再提醒。 */
function dismiss() {
  dismissed.value = true
  readError.value = false
}

/** 横幅面板：测量高度用。 */
const panelRef = ref<HTMLElement | null>(null)
let heightObserver: ResizeObserver | null = null

function openInbox() {
  void router.push('/notifications')
}

watch(
  accountKey,
  () => {
    resetForAccount()
    // 绑定账号会立刻拉一次已读映射（体积小），其它入口能马上拿到未读真源。
    bindNotificationAccount(accountKey.value)
    if (!accountKey.value) {
      return
    }
    readyAt = Date.now() + INITIAL_DELAY_MS
    initialTimer = setTimeout(() => {
      readyAt = 0
      initialTimer = null
      void checkNotifications(true)
    }, INITIAL_DELAY_MS)
  },
  { immediate: true }
)

// 路由变化本身不是高频刷新点：用 60s 节流覆盖「新页面 / 再次进入」。
watch(
  () => route.path,
  () => {
    void checkNotifications()
  }
)

function onVisibilityChange() {
  if (document.visibilityState === 'visible') {
    void checkNotifications()
  }
}

/** Esc 收起横幅（不抢焦点：只有横幅可见时才拦这个键）。 */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !current.value) {
    return
  }
  dismiss()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('keydown', onKeydown)
  if (typeof ResizeObserver !== 'undefined' && panelRef.value) {
    heightObserver = new ResizeObserver(() => syncBannerOffset())
    heightObserver.observe(panelRef.value)
  }
})

/**
 * 横幅出现时把内容区往下推一条横幅的高度：
 * 顶部消息框不该长期压住页面标题（页头、切角面板都会被盖住半截）。
 * 高度用 ResizeObserver 量，正文变长时也能跟上。
 */
function syncBannerOffset() {
  const height = panelRef.value?.offsetHeight ?? 0
  document.documentElement.style.setProperty(
    '--zn-banner-offset',
    height ? `${height}px` : '0px'
  )
}

watch(current, (notice) => {
  if (!notice) {
    document.documentElement.style.setProperty('--zn-banner-offset', '0px')
    return
  }
  void nextTick(syncBannerOffset)
})

onBeforeUnmount(() => {
  heightObserver?.disconnect()
  heightObserver = null
  document.documentElement.style.setProperty('--zn-banner-offset', '0px')
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('keydown', onKeydown)
  resetForAccount()
})
</script>

<template>
  <div
    v-if="current"
    class="zn-banner"
    role="region"
    :aria-label="t('notificationCenter.bannerTitle')"
  >
    <div ref="panelRef" class="zn-banner__panel">
      <div class="zn-banner__head">
        <span class="zn-banner__tag">{{ t('notificationCenter.bannerTag') }}</span>
        <span class="zn-banner__count">{{
          t('notificationCenter.progress', { current: 1, total })
        }}</span>
        <button
          type="button"
          class="zn-banner__close"
          :aria-label="t('notificationCenter.close')"
          @click="dismiss"
        >
          ×
        </button>
      </div>

      <div class="zn-banner__body">
        <h2 class="zn-banner__title">{{ current.title }}</h2>
        <p class="zn-banner__meta">
          {{ t('notificationCenter.from', { name: current.author_name || '—' }) }}
          <span class="zn-banner__dot" aria-hidden="true">·</span>
          {{ formatDateTime(current.created_at) }}
        </p>
        <div class="zn-banner__content">
          <ZMarkdown :source="current.content" />
        </div>
      </div>

      <p v-if="readError" class="zn-banner__error" role="alert">
        {{ t('notificationCenter.readFailed') }}
        <button type="button" class="zn-banner__retry" @click="acknowledge">
          {{ t('notificationCenter.retry') }}
        </button>
      </p>

      <div class="zn-banner__actions">
        <button type="button" class="zn-banner__link" @click="openInbox">
          {{ t('notificationCenter.viewAll') }}
        </button>
        <button
          type="button"
          class="zn-banner__btn"
          :disabled="advancing"
          @click="suppress"
        >
          {{ t('notificationCenter.suppress') }}
        </button>
        <button
          type="button"
          class="zn-banner__btn zn-banner__btn--primary"
          :disabled="advancing"
          @click="acknowledge"
        >
          {{ t('notificationCenter.read') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统 · 顶部通知横幅
   - 直角 + 右上斜切，细线描边，强调色只用在标签与主操作上
   - 面板底色不透明：横幅压着页面内容，透底会读不清
   - 深浅两套主题都靠 --yg-* 令牌自适应，不写死颜色
   ========================================================================== */
.zn-banner {
  --zn-accent: var(--yg-color-primary);
  --zn-ink: var(--yg-text-primary);
  --zn-muted: var(--yg-text-tertiary);
  --zn-surface: var(--yg-bg-surface-raised);
  --zn-line: var(--yg-border-default);
  --zn-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);

  position: fixed;
  inset-block-start: 0;
  inset-inline: 0;
  z-index: var(--yg-z-sticky);
  display: flex;
  justify-content: center;
  padding: var(--yg-space-3) var(--yg-space-3) 0;
  /* 横幅之外的区域照常可点：它不是遮罩，不该拦住页面操作。 */
  pointer-events: none;
}

.zn-banner__panel {
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  width: min(680px, 100%);
  max-height: min(72vh, 620px);
  padding: var(--yg-space-4) var(--yg-space-5) var(--yg-space-4);
  overflow: hidden;
  color: var(--zn-ink);
  background-color: var(--zn-surface);
  border: 1px solid var(--zn-line);
  /* 斜切轮廓会裁掉 box-shadow，投影改用 drop-shadow 跟着轮廓走。 */
  clip-path: polygon(
    0 0,
    calc(100% - 20px) 0,
    100% 20px,
    100% 100%,
    20px 100%,
    0 calc(100% - 20px)
  );
  filter: drop-shadow(0 10px 24px rgba(5, 6, 8, 0.28));
  animation: zn-banner-in 260ms cubic-bezier(0.19, 1, 0.22, 1) both;
}

/* 顶部一条强调色刻线：横幅从页面顶端「压」出来的视觉锚点。 */
.zn-banner__panel::before {
  content: '';
  position: absolute;
  inset-block-start: 0;
  inset-inline-start: 0;
  width: 96px;
  height: 2px;
  background: var(--zn-accent);
  transform: skewX(-40deg);
  transform-origin: left;
}

.zn-banner__head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  padding-inline-start: var(--yg-space-1);
}

.zn-banner__tag {
  padding: 2px 8px 1px;
  color: var(--zn-accent);
  background: color-mix(in srgb, var(--zn-accent) 14%, transparent);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  clip-path: polygon(0 0, calc(100% - 5px) 0, 100% 5px, 100% 100%, 0 100%);
}

.zn-banner__count {
  color: var(--zn-muted);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.06em;
  font-variant-numeric: tabular-nums;
}

.zn-banner__close {
  margin-inline-start: auto;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--zn-muted);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  transition: color 160ms cubic-bezier(0.19, 1, 0.22, 1);
}

.zn-banner__close:hover {
  color: var(--zn-ink);
}

.zn-banner__close:focus-visible {
  outline: 2px solid var(--zn-accent);
  outline-offset: 2px;
}

.zn-banner__body {
  min-height: 0;
  overflow-y: auto;
}

.zn-banner__title {
  margin: 0;
  color: var(--zn-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  overflow-wrap: anywhere;
}

.zn-banner__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  margin: var(--yg-space-1) 0 0;
  color: var(--zn-muted);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.zn-banner__dot {
  color: var(--yg-border-strong);
}

.zn-banner__content {
  margin-top: var(--yg-space-3);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
  overflow-wrap: anywhere;
}

.zn-banner__content :deep(p:last-child) {
  margin-bottom: 0;
}

.zn-banner__error {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0;
  padding: var(--yg-space-2) var(--yg-space-3);
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
  font-size: var(--yg-font-size-sm);
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
}

.zn-banner__retry {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: var(--yg-font-weight-semibold);
  text-decoration: underline;
  cursor: pointer;
}

.zn-banner__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  padding-top: var(--yg-space-1);
  border-top: 1px solid var(--yg-border-subtle);
}

.zn-banner__link {
  margin-inline-end: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--zn-accent);
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
}

.zn-banner__link:hover {
  text-decoration: underline;
}

.zn-banner__btn {
  min-height: 36px;
  padding: 0 var(--yg-space-4);
  border: 1px solid var(--zn-line);
  background: transparent;
  color: var(--zn-ink);
  font-family: inherit;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  cursor: pointer;
  transition:
    border-color 160ms cubic-bezier(0.19, 1, 0.22, 1),
    background-color 160ms cubic-bezier(0.19, 1, 0.22, 1);
}

.zn-banner__btn:hover:not(:disabled) {
  border-color: var(--yg-border-strong);
  background: var(--yg-bg-hover);
}

.zn-banner__btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.zn-banner__btn--primary {
  color: var(--yg-bg-page);
  background: var(--zn-accent);
  border-color: transparent;
  font-weight: var(--yg-font-weight-semibold);
}

.zn-banner__btn--primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--zn-accent) 84%, #000);
}

.zn-banner__btn:focus-visible,
.zn-banner__link:focus-visible,
.zn-banner__retry:focus-visible {
  outline: 2px solid var(--zn-accent);
  outline-offset: 2px;
}

@keyframes zn-banner-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 0, 0 0, 0 0, 0 0);
  }
  to {
    opacity: 1;
    clip-path: polygon(
      0 0,
      calc(100% - 20px) 0,
      100% 20px,
      100% 100%,
      20px 100%,
      0 calc(100% - 20px)
    );
  }
}

/* 减少动态：只保留静态呈现，不做入场位移动画。 */
@media (prefers-reduced-motion: reduce) {
  .zn-banner__panel {
    animation: none;
  }

  .zn-banner__close,
  .zn-banner__btn {
    transition: none;
  }
}

/* ==========================================================================
   内容让位：横幅出现时把滚动内容整体下移一条横幅的高度。
   这段不进 scoped（要作用到 .yg-layout__content），但只依赖本组件写出的
   --zn-banner-offset 变量，不会波及其它页面。offset 为 0 时 padding-top 与
   base.css 的原值一致，视觉上等于「没有横幅」。
   ========================================================================== */
:global(.yg-layout__content) {
  transition: padding-top 220ms cubic-bezier(0.19, 1, 0.22, 1);
  padding-top: calc(var(--yg-space-6) + var(--zn-banner-offset, 0px));
}

@media (prefers-reduced-motion: reduce) {
  :global(.yg-layout__content) {
    transition: none;
  }
}

@media (max-width: 599px) {
  .zn-banner {
    padding: var(--yg-space-2) var(--yg-space-2) 0;
  }

  .zn-banner__panel {
    padding: var(--yg-space-3) var(--yg-space-4);
  }

  .zn-banner__actions {
    gap: var(--yg-space-2);
  }

  .zn-banner__link {
    width: 100%;
    margin-inline-end: 0;
    text-align: start;
  }

  .zn-banner__btn {
    flex: 1 1 0;
    min-width: 0;
  }
}
</style>

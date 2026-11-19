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
 * 队列：服务端返回的未读通知逐条展示，头部给出「第 N / M 条」与分段刻度。
 * 「已读」写穿服务端（乐观更新 + 失败回滚），「不再显示」只落本地。
 *
 * 退场与计数只是展示层状态：收起 / 消化掉最后一条时，面板先播完反向动画
 * 再从 DOM 摘除；N / M 记的是本次会话实际展示过几条、最多见过几条。
 * 两者都不触碰队列取数、已读写穿与本地落库的语义。
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
/** 退场动画 300ms 播完后留 40ms 余量再摘 DOM，避免尾帧被掐掉。 */
const LEAVE_MS = 340
/** 队列刻度最多画几枚：超长队列由「第 N / M 条」文本兜底，不无限占宽。 */
const MAX_TICKS = 9

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

/** 「第 N / M 条」：纯展示计数。会话从横幅出现开始，到完全隐藏为止。 */
const shownCount = ref(0)
const sessionTotal = ref(0)

/** 分段刻度（多条通知时的切换指示）：active 是当前这条，done 是已消化。 */
const tickTotal = computed(() => Math.min(sessionTotal.value, MAX_TICKS))
const tickActive = computed(() => Math.min(shownCount.value, MAX_TICKS))

/**
 * 退场快照：收起 / 队列清空时 current 已经变 null，但面板还要播完反向动画，
 * 这段时间渲染的是 leavingNotice 的只读快照（不可交互）。
 */
const leaving = ref(false)
const leavingNotice = ref<NotificationRecord | null>(null)
let leaveTimer: ReturnType<typeof setTimeout> | null = null

/** 面板实际渲染的那条通知：正常取队列头部，退场期沿用最后一条的快照。 */
const view = computed<NotificationRecord | null>(() =>
  current.value ?? (leaving.value ? leavingNotice.value : null)
)

function beginLeave(notice: NotificationRecord) {
  leavingNotice.value = notice
  leaving.value = true
  if (leaveTimer) {
    clearTimeout(leaveTimer)
  }
  leaveTimer = setTimeout(() => {
    leaveTimer = null
    leaving.value = false
    leavingNotice.value = null
  }, LEAVE_MS)
}

function clearLeave() {
  if (leaveTimer) {
    clearTimeout(leaveTimer)
    leaveTimer = null
  }
  leaving.value = false
  leavingNotice.value = null
}

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
  shownCount.value = 0
  sessionTotal.value = 0
  clearLeave()
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

/**
 * 面板随 v-if 挂载/摘除，高度观察也要跟着面板走：
 * 出现时挂上（markdown 是异步组件，正文晚到也能跟上高度），隐藏时拆掉。
 */
function attachHeightObserver() {
  if (heightObserver || typeof ResizeObserver === 'undefined' || !panelRef.value) {
    return
  }
  heightObserver = new ResizeObserver(() => syncBannerOffset())
  heightObserver.observe(panelRef.value)
}

function detachHeightObserver() {
  heightObserver?.disconnect()
  heightObserver = null
}

function openInbox() {
  void router.push('/notifications')
}

watch(
  accountKey,
  () => {
    resetForAccount()
    // 绑定账号会立刻拉一次已读映射（体积小），且所有入口都要靠它判断未读。
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

// 队列推进只做展示层的两件事：计数 + 退场快照。写穿与落库逻辑一字未动。
watch(current, (notice, prev) => {
  if (notice) {
    clearLeave()
    if (!prev) {
      shownCount.value = 1
      sessionTotal.value = total.value
    } else if (notice.id !== prev.id) {
      shownCount.value += 1
      sessionTotal.value = Math.max(sessionTotal.value, total.value)
    } else {
      sessionTotal.value = Math.max(sessionTotal.value, total.value)
    }
    return
  }
  if (prev) {
    beginLeave(prev)
  }
})

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
})

/**
 * 横幅出现时把内容区往下推一条横幅的高度：
 * 顶部消息框不该长期压住页面标题（页头、切角面板都会被盖住半截）。
 * 让位跟着「看得见的那块面板」走：退场动画期间内容保持让位，
 * 面板真正摘除后才平滑收回，避免正文在动画半途就顶上来。
 */
function syncBannerOffset() {
  const height = panelRef.value?.offsetHeight ?? 0
  document.documentElement.style.setProperty(
    '--zn-banner-offset',
    height ? `${height}px` : '0px'
  )
}

watch(view, (notice) => {
  if (!notice) {
    detachHeightObserver()
    document.documentElement.style.setProperty('--zn-banner-offset', '0px')
    return
  }
  void nextTick(() => {
    attachHeightObserver()
    syncBannerOffset()
  })
})

onBeforeUnmount(() => {
  clearLeave()
  detachHeightObserver()
  document.documentElement.style.setProperty('--zn-banner-offset', '0px')
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('keydown', onKeydown)
  resetForAccount()
})
</script>

<template>
  <div
    v-if="view"
    class="zn-banner"
    role="region"
    :aria-label="t('notificationCenter.bannerTitle')"
  >
    <section
      ref="panelRef"
      class="zn-banner__panel"
      :class="{ 'is-leaving': leaving }"
    >
      <!-- 左侧强调色竖轨：竖排 HUD 标签，是横幅最显眼的轮廓变化。 -->
      <div class="zn-banner__rail" aria-hidden="true">
        <span class="zn-banner__rail-tag">{{ t('notificationCenter.bannerTag') }}</span>
        <span class="zn-banner__rail-line"></span>
      </div>

      <div class="zn-banner__main">
        <header class="zn-banner__head">
          <span class="zn-banner__count">{{
            t('notificationCenter.progress', { current: shownCount, total: sessionTotal })
          }}</span>
          <!-- 分段刻度：多条通知时的切换指示，第 N 条亮起、之后的还空着。 -->
          <ul
            v-if="tickTotal > 1"
            class="zn-banner__ticks"
            aria-hidden="true"
          >
            <li
              v-for="tick in tickTotal"
              :key="tick"
              class="zn-banner__tick"
              :class="{
                'is-active': tick === tickActive,
                'is-done': tick < tickActive
              }"
            ></li>
          </ul>
          <button
            type="button"
            class="zn-banner__close"
            :aria-label="t('notificationCenter.close')"
            @click="dismiss"
          >
            <span class="zn-banner__close-glyph" aria-hidden="true">✕</span>
          </button>
        </header>

        <div class="zn-banner__swap">
          <Transition name="zn-swap">
            <article :key="view.id" class="zn-banner__sheet">
              <h2 class="zn-banner__title">{{ view.title }}</h2>
              <p class="zn-banner__meta">
                <span class="zn-banner__meta-item">{{
                  t('notificationCenter.from', { name: view.author_name || '—' })
                }}</span>
                <span class="zn-banner__meta-sep" aria-hidden="true"></span>
                <span class="zn-banner__meta-item">{{ formatDateTime(view.created_at) }}</span>
              </p>
              <div class="zn-banner__content">
                <ZMarkdown :source="view.content" />
              </div>
            </article>
          </Transition>
        </div>

        <p v-if="readError" class="zn-banner__error" role="alert">
          {{ t('notificationCenter.readFailed') }}
          <button type="button" class="zn-banner__retry" @click="acknowledge">
            {{ t('notificationCenter.retry') }}
          </button>
        </p>

        <footer class="zn-banner__actions">
          <button type="button" class="zn-banner__link" @click="openInbox">
            {{ t('notificationCenter.viewAll') }}
            <span class="zn-banner__link-arrow" aria-hidden="true">→</span>
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
        </footer>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统 · 顶部通知横幅（函件式重做）
   - 轮廓：左侧强调色竖轨 + 竖排 HUD 标签，替代旧的「居中对称卡片」
   - 层级：头部一条沉底信息条（计数 / 刻度 / 收起），正文独立可滚动
   - 队列指示：斜切分段刻度，当前条亮、未读条空，一眼看出还剩几条
   - 动效：入场按「竖轨 → 头部 → 正文 → 操作」编排展开；退场反向收拢
   - 深浅两套主题都靠 --yg-* 令牌自适应，不写死颜色
   ========================================================================== */
.zn-banner {
  --zn-accent: var(--yg-color-primary);
  --zn-ink: var(--yg-text-primary);
  --zn-muted: var(--yg-text-tertiary);
  --zn-surface: var(--yg-bg-surface-raised);
  --zn-sunken: var(--yg-bg-surface-sunken);
  --zn-line: var(--yg-border-default);
  --zn-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  --zn-ease: cubic-bezier(0.19, 1, 0.22, 1);

  position: fixed;
  inset-block-start: 0;
  inset-inline: 0;
  z-index: var(--yg-z-sticky);
  display: flex;
  justify-content: center;
  /* 刘海 / 灵动岛 / 手势条：安全区先让位，再叠加常规留白。 */
  padding:
    calc(env(safe-area-inset-top, 0px) + var(--yg-space-3))
    calc(env(safe-area-inset-right, 0px) + var(--yg-space-3))
    0
    calc(env(safe-area-inset-left, 0px) + var(--yg-space-3));
  /* 横幅之外的区域照常可点：它不是遮罩，不该拦住页面操作。 */
  pointer-events: none;
}

/* 面板本体：竖轨 + 主列的两栏构图。右下角一枚大切角呼应全站棱角语言，
   柔和双层投影走语义令牌，不与切角轮廓打架。 */
.zn-banner__panel {
  pointer-events: auto;
  position: relative;
  isolation: isolate;
  display: flex;
  align-items: stretch;
  width: min(720px, 100%);
  max-height: min(72vh, 620px);
  overflow: hidden;
  color: var(--zn-ink);
  background-color: var(--zn-surface);
  border: 1px solid var(--zn-line);
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 18px), calc(100% - 18px) 100%, 0 100%);
  box-shadow: var(--yg-shadow-lg);
  animation: zn-panel-in var(--yg-dur-slow) var(--zn-ease) both;
}

/* 收起 / 消化掉最后一条：反向播放入场动画，播完由脚本摘除 DOM。 */
.zn-banner__panel.is-leaving {
  pointer-events: none;
  animation: zn-panel-out var(--yg-dur-slow) var(--zn-ease) both;
}

/* ---- 左侧竖轨：强调色渐隐到底，竖排 HUD 标签顺着轨走 ---- */
.zn-banner__rail {
  position: relative;
  flex: 0 0 auto;
  width: 36px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-3);
  padding: var(--yg-space-4) 0 var(--yg-space-3);
  border-inline-end: 1px solid var(--yg-border-subtle);
  background:
    linear-gradient(
      to bottom,
      color-mix(in srgb, var(--zn-accent) 16%, transparent),
      color-mix(in srgb, var(--zn-accent) 3%, transparent) 62%,
      transparent
    );
  animation: zn-rail-in var(--yg-dur-slow) var(--zn-ease) both;
}

.zn-banner__rail-tag {
  color: var(--zn-accent);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.3em;
  text-transform: uppercase;
  writing-mode: vertical-rl;
  white-space: nowrap;
}

.zn-banner__rail-line {
  flex: 1 1 auto;
  width: 2px;
  min-height: 0;
  background: linear-gradient(
    to bottom,
    color-mix(in srgb, var(--zn-accent) 55%, transparent),
    transparent
  );
  transform: skewX(-40deg);
}

/* ---- 主列 ---- */
.zn-banner__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* 头部沉底信息条：计数、刻度、收起聚在一层浅坑里，和正文拉开层级。 */
.zn-banner__head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  padding: var(--yg-space-2) var(--yg-space-4);
  border-block-end: 1px solid var(--yg-border-subtle);
  background: var(--zn-sunken);
  animation: zn-layer-in var(--yg-dur-slow) var(--zn-ease) 60ms both;
}

.zn-banner__count {
  color: var(--zn-muted);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.06em;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 分段刻度：斜切平行四边形，进行中的实心发亮，已消化的沉底，未到的空心。 */
.zn-banner__ticks {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.zn-banner__tick {
  width: 16px;
  height: 5px;
  border: 1px solid var(--yg-border-strong);
  transform: skewX(-40deg);
  transition:
    background-color var(--yg-dur-base) var(--zn-ease),
    border-color var(--yg-dur-base) var(--zn-ease),
    box-shadow var(--yg-dur-base) var(--zn-ease);
}

.zn-banner__tick.is-done {
  border-color: transparent;
  background: color-mix(in srgb, var(--zn-accent) 32%, transparent);
}

.zn-banner__tick.is-active {
  border-color: var(--zn-accent);
  background: var(--zn-accent);
  box-shadow: 0 0 8px color-mix(in srgb, var(--zn-accent) 55%, transparent);
}

.zn-banner__close {
  margin-inline-start: auto;
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  background: transparent;
  color: var(--zn-muted);
  cursor: pointer;
  transition:
    color var(--yg-dur-fast) var(--zn-ease),
    border-color var(--yg-dur-fast) var(--zn-ease),
    background-color var(--yg-dur-fast) var(--zn-ease);
}

.zn-banner__close:hover {
  color: var(--zn-ink);
  border-color: var(--yg-border-strong);
  background: var(--yg-bg-hover);
}

.zn-banner__close-glyph {
  font-size: 12px;
  line-height: 1;
}

.zn-banner__close:focus-visible {
  outline: 2px solid var(--zn-accent);
  outline-offset: 2px;
}

/* ---- 正文：独立滚动区，长 markdown 压不扁操作 ---- */
.zn-banner__swap {
  min-height: 0;
  overflow-y: auto;
  padding: var(--yg-space-4) var(--yg-space-5);
}

.zn-banner__sheet {
  animation: zn-layer-in var(--yg-dur-slow) var(--zn-ease) 120ms both;
}

.zn-banner__title {
  margin: 0;
  color: var(--zn-ink);
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  overflow-wrap: anywhere;
}

.zn-banner__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  margin: var(--yg-space-2) 0 0;
}

.zn-banner__meta-item {
  color: var(--zn-muted);
  font-family: var(--zn-hud), var(--yg-font-sans);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

/* 发布人 / 时间中间的斜切短线：呼应竖轨与刻度的棱角记号。 */
.zn-banner__meta-sep {
  width: 10px;
  height: 2px;
  background: var(--zn-accent);
  opacity: 0.65;
  transform: skewX(-40deg);
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

/* 队列切换（第 N → N+1 条）：旧文上移淡出、新文下移淡入。 */
.zn-swap-enter-active,
.zn-swap-leave-active {
  transition:
    opacity var(--yg-dur-base) var(--zn-ease),
    transform var(--yg-dur-base) var(--zn-ease);
}

.zn-swap-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.zn-swap-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.zn-swap-leave-active {
  position: absolute;
  inset-inline: var(--yg-space-5);
  pointer-events: none;
}

/* ---- 写穿失败：沉底警示条，重试是下划线动作 ---- */
.zn-banner__error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0;
  padding: var(--yg-space-2) var(--yg-space-5);
  border-block-start: 1px solid color-mix(in srgb, var(--yg-text-danger) 32%, transparent);
  color: var(--yg-text-danger);
  background: var(--yg-color-danger-soft);
  font-size: var(--yg-font-size-sm);
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

.zn-banner__retry:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}

/* ---- 操作区：细线分界，主操作是唯一实底强调块 ---- */
.zn-banner__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  margin-block-start: auto;
  padding: var(--yg-space-3) var(--yg-space-5);
  border-block-start: 1px solid var(--yg-border-subtle);
  animation: zn-layer-in var(--yg-dur-slow) var(--zn-ease) 180ms both;
}

.zn-banner__link {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  margin-inline-end: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--zn-accent);
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
  transition: gap var(--yg-dur-fast) var(--zn-ease);
}

.zn-banner__link:hover {
  text-decoration: underline;
}

.zn-banner__link:hover .zn-banner__link-arrow {
  transform: translateX(2px);
}

.zn-banner__link-arrow {
  transition: transform var(--yg-dur-fast) var(--zn-ease);
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
  clip-path: polygon(0 0, calc(100% - 7px) 0, 100% 7px, 100% 100%, 0 100%);
  transition:
    border-color var(--yg-dur-fast) var(--zn-ease),
    background-color var(--yg-dur-fast) var(--zn-ease),
    color var(--yg-dur-fast) var(--zn-ease);
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
.zn-banner__link:focus-visible {
  outline: 2px solid var(--zn-accent);
  outline-offset: 2px;
}

/* ---- 进出场编排 ---- */
@keyframes zn-panel-in {
  from {
    opacity: 0;
    transform: translateY(-14px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes zn-panel-out {
  from {
    opacity: 1;
    transform: translateY(0);
  }
  to {
    opacity: 0;
    transform: translateY(-14px);
  }
}

/* 竖轨自顶向下铺开：强调色先立住，正文随后入场。 */
@keyframes zn-rail-in {
  from {
    opacity: 0;
    transform: scaleY(0.2);
  }
  to {
    opacity: 1;
    transform: scaleY(1);
  }
}

@keyframes zn-layer-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 减少动态：只保留静态呈现，编排动画全部关闭。 */
@media (prefers-reduced-motion: reduce) {
  .zn-banner__panel,
  .zn-banner__rail,
  .zn-banner__head,
  .zn-banner__sheet,
  .zn-banner__actions {
    animation: none;
  }

  .zn-swap-enter-active,
  .zn-swap-leave-active {
    transition: none;
  }

  .zn-banner__tick,
  .zn-banner__btn,
  .zn-banner__close,
  .zn-banner__link-arrow {
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
  transition: padding-top var(--yg-dur-slow) var(--zn-ease);
  padding-top: calc(var(--yg-space-6) + var(--zn-banner-offset, 0px));
}

@media (prefers-reduced-motion: reduce) {
  :global(.yg-layout__content) {
    transition: none;
  }
}

/* ---- 竖屏 / 窄屏：竖轨收窄，长文滚动照旧，操作按钮铺满一行 ---- */
@media (max-width: 599px) {
  .zn-banner {
    padding:
      calc(env(safe-area-inset-top, 0px) + var(--yg-space-2))
      calc(env(safe-area-inset-right, 0px) + var(--yg-space-2))
      0
      calc(env(safe-area-inset-left, 0px) + var(--yg-space-2));
  }

  .zn-banner__rail {
    width: 24px;
    padding: var(--yg-space-3) 0 var(--yg-space-2);
  }

  .zn-banner__rail-tag {
    display: none;
  }

  .zn-banner__head {
    gap: var(--yg-space-2);
    padding: var(--yg-space-2) var(--yg-space-3);
  }

  .zn-banner__tick {
    width: 12px;
  }

  .zn-banner__swap {
    padding: var(--yg-space-3) var(--yg-space-4);
  }

  .zn-banner__actions {
    gap: var(--yg-space-2);
    padding: var(--yg-space-3) var(--yg-space-4);
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

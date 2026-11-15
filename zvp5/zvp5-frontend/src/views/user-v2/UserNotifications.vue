<script setup lang="ts">
/**
 * 站内通知收件箱。
 *
 * 列出平台当前发出的全部通知（已读 + 未读都可见），未读用强调色六边形角标标出；
 * 点开看正文（markdown），也可以就地标记已读——已读写穿服务端，跟着账号走，
 * 换设备登录同样生效。
 *
 * 管理员不在导航里出现这个入口：他们有 /manage/notifications 的编辑 + 发送页，
 * 两处放同一个「通知」入口容易混淆。直接访问本路由仍然可以正常工作。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { Bell, Refresh, Warning } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { formatDateTime } from '@/utils/dateTime'
import {
  bindNotificationAccount,
  isUnreadNotification,
  markNotificationRead,
  notificationRuntime,
  suppressNotificationLocally,
  syncNotifications
} from '@/components/notification/notificationStore'
import type { NotificationRecord } from '@/api/notifications'

// 正文渲染按需加载：markdown-it + dompurify 不进本页的首包。
const ZMarkdown = defineAsyncComponent(() => import('@/components/markdown/ZMarkdown.vue'))

const { t } = useI18n()
const user = useUserStore()
const header = useHeaderStore()

header.setHeader(t('notificationCenter.page.title'))

const accountKey = computed(() => String(user._id || user.id || ''))
const openId = ref('')
const marking = ref(false)

/** 全部现役通知（已读 + 未读都列出）。 */
const notices = computed<NotificationRecord[]>(() => notificationRuntime.notifications)
const unreadTotal = computed(() => notices.value.filter((notice) => isUnreadNotification(notice)).length)

function isUnread(notice: NotificationRecord): boolean {
  return isUnreadNotification(notice)
}

function toggle(id: string) {
  openId.value = openId.value === id ? '' : id
}

/** 展开即标记已读：写穿服务端，失败时给出可重试的提示。 */
async function markRead(notice: NotificationRecord) {
  if (marking.value || !isUnread(notice)) {
    return
  }
  marking.value = true
  try {
    await markNotificationRead(notice)
  } finally {
    marking.value = false
  }
}

/** 「不再显示」只在本机当前账号生效，不影响其它设备或其它账号。 */
function suppress(notice: NotificationRecord) {
  suppressNotificationLocally(notice)
}

async function refresh() {
  await syncNotifications()
}

onMounted(async () => {
  bindNotificationAccount(accountKey.value)
  await syncNotifications()
})
</script>

<template>
  <div class="notif-page">
    <header class="phx-head">
      <div class="phx-head__copy">
        <p class="phx-head__eyebrow">Notice · {{ unreadTotal }}</p>
        <h1 class="phx-head__title">{{ t('notificationCenter.page.title') }}</h1>
        <p class="phx-head__lead">{{ t('notificationCenter.page.lead') }}</p>
      </div>
      <span class="phx-head__index" aria-hidden="true">06</span>
    </header>

    <section class="notif-panel" style="--phx-i: 1">
      <div class="notif-panel__head">
        <span class="notif-panel__code" aria-hidden="true">MSG</span>
        <h2 class="notif-panel__title">{{ t('notificationCenter.page.listTitle') }}</h2>
        <span v-if="unreadTotal > 0" class="notif-panel__badge">
          {{ t('notificationCenter.page.unreadCount', { count: unreadTotal }) }}
        </span>
        <button
          type="button"
          class="notif-panel__refresh"
          :disabled="notificationRuntime.loading"
          @click="refresh"
        >
          <ElIcon><Refresh /></ElIcon>
          <span>{{ t('notificationCenter.page.refresh') }}</span>
        </button>
      </div>

      <div class="notif-panel__body">
        <div
          v-if="notificationRuntime.loading && notices.length === 0"
          class="notif-skeleton"
          role="status"
          :aria-label="t('notificationCenter.page.loading')"
        >
          <span class="notif-skeleton__text" aria-hidden="true"></span>
          <span class="notif-skeleton__text" aria-hidden="true"></span>
          <span class="notif-skeleton__text" aria-hidden="true"></span>
        </div>

        <div v-else-if="notificationRuntime.failed" class="notif-empty">
          <div class="notif-empty__icon" aria-hidden="true">
            <ElIcon><Warning /></ElIcon>
          </div>
          <p class="notif-empty__title">{{ t('notificationCenter.page.failed') }}</p>
          <p class="notif-empty__desc">{{ t('notificationCenter.page.failedDesc') }}</p>
          <div class="notif-empty__actions">
            <button type="button" class="notif-btn" @click="refresh">
              {{ t('notificationCenter.page.retry') }}
            </button>
          </div>
        </div>

        <div v-else-if="notices.length === 0" class="notif-empty">
          <div class="notif-empty__icon" aria-hidden="true">
            <ElIcon><Bell /></ElIcon>
          </div>
          <p class="notif-empty__title">{{ t('notificationCenter.page.empty') }}</p>
          <p class="notif-empty__desc">{{ t('notificationCenter.page.emptyDesc') }}</p>
        </div>

        <ul v-else class="notif-list">
          <li
            v-for="(notice, index) in notices"
            :key="notice.id"
            class="notif-item"
            :class="{ 'is-unread': isUnread(notice) }"
            :style="{ '--phx-n': Math.min(index, 8) }"
          >
            <button
              type="button"
              class="notif-item__head"
              :aria-expanded="openId === notice.id"
              @click="toggle(notice.id)"
            >
              <span
                class="notif-item__badge"
                :class="{ 'is-unread': isUnread(notice) }"
                role="img"
                :aria-label="
                  isUnread(notice)
                    ? t('notificationCenter.page.unread')
                    : t('notificationCenter.page.read')
                "
              ></span>
              <span class="notif-item__title">{{ notice.title }}</span>
              <span class="notif-item__meta">
                {{ notice.author_name || t('notificationCenter.page.sender') }} ·
                {{ formatDateTime(notice.created_at) }}
              </span>
              <span class="notif-item__state">
                {{
                  isUnread(notice)
                    ? t('notificationCenter.page.unread')
                    : t('notificationCenter.page.read')
                }}
              </span>
            </button>

            <div v-if="openId === notice.id" class="notif-item__body">
              <div class="notif-item__content">
                <ZMarkdown :source="notice.content" />
              </div>
              <div class="notif-item__actions">
                <button
                  v-if="isUnread(notice)"
                  type="button"
                  class="notif-btn notif-btn--primary"
                  :disabled="marking"
                  @click="markRead(notice)"
                >
                  {{ t('notificationCenter.page.markRead') }}
                </button>
                <button type="button" class="notif-btn" @click="suppress(notice)">
                  {{ t('notificationCenter.suppress') }}
                </button>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统 · 站内通知收件箱
   - 与 user-v2 其它页面同一套切角面板 + HUD 标签语言
   - 未读用强调色六边形角标（clip-path，无图片）
   ========================================================================== */
.notif-page {
  --phx-accent: var(--yg-color-primary);
  --phx-ink: var(--yg-text-primary);
  --phx-muted: var(--yg-text-tertiary);
  --phx-surface: var(--yg-bg-surface);
  --phx-sunken: var(--yg-bg-surface-sunken);
  --phx-line: color-mix(in srgb, var(--phx-accent) 22%, transparent);
  --phx-line-soft: color-mix(in srgb, var(--phx-ink) 10%, transparent);
  --phx-hard: color-mix(in srgb, var(--phx-accent) 16%, #000);
  --phx-display: var(--phi-display, Bebas, Impact, sans-serif);
  --phx-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  --phx-ease: cubic-bezier(0.19, 1, 0.22, 1);
  --phx-spring: cubic-bezier(0.34, 1.4, 0.64, 1);
  --yg-radius-xs: 0;
  --yg-radius-sm: 0;
  --yg-radius-md: 0;
  --yg-radius-lg: 0;
  --yg-radius-xl: 0;

  display: flex;
  flex-direction: column;
  gap: var(--yg-space-6);
  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  padding: var(--yg-space-6);
}

html[data-theme='light'] .notif-page {
  --phx-accent: #007a70;
  --phx-hard: #101418;
}

/* ---------- 页头：HUD 编号 + 细网格 + 斜线 ---------- */
.phx-head {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: var(--yg-space-4);
  margin: 0;
  padding: var(--yg-space-6) var(--yg-space-6) var(--yg-space-5);
  overflow: hidden;
  border-left: 3px solid var(--phx-accent);
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phx-accent) 50%,
        transparent calc(50% + 1.5px)
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(
      100deg,
      color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface)),
      var(--phx-surface) 58%
    );
  clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  animation: notif-head-in 420ms var(--phx-ease) both;
}

.phx-head::after {
  content: '';
  position: absolute;
  right: 18%;
  bottom: 0;
  width: 120px;
  height: 6px;
  background: repeating-linear-gradient(-55deg, var(--phx-accent) 0 2px, transparent 2px 7px);
  opacity: 0.55;
  pointer-events: none;
}

.phx-head__copy {
  min-width: 0;
}

.phx-head__eyebrow {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0 0 var(--yg-space-2);
  color: var(--phx-accent);
  font: 400 15px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

.phx-head__eyebrow::before {
  content: '';
  width: 18px;
  height: 2px;
  background: currentColor;
  transform: skewX(-40deg);
}

.phx-head__title {
  position: relative;
  display: inline-block;
  margin: 0;
  color: var(--phx-ink);
  font-size: clamp(26px, 4.2vw, 38px);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1.1;
  letter-spacing: 0.02em;
  animation: notif-slide-in 420ms 60ms var(--phx-ease) both;
}

.phx-head__title::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 0;
  width: 44px;
  height: 3px;
  background: var(--phx-accent);
  transform: skewX(-40deg);
  transform-origin: left;
}

.phx-head__lead {
  max-width: 54ch;
  margin: var(--yg-space-4) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
  animation: notif-slide-in 420ms 110ms var(--phx-ease) both;
}

.phx-head__index {
  align-self: start;
  color: transparent;
  font: 400 clamp(64px, 11vw, 112px) / 0.8 var(--phx-display);
  -webkit-text-stroke: 1px color-mix(in srgb, var(--phx-ink) 45%, transparent);
  user-select: none;
  animation: notif-slide-in-r 450ms 40ms var(--phx-ease) both;
}

/* ---------- 切角面板 ---------- */
.notif-panel {
  --phx-cut: 18px;
  --phx-cut-s: 8px;
  position: relative;
  isolation: isolate;
  color: var(--yg-text-secondary);
  filter: drop-shadow(5px 5px 0 var(--phx-hard));
  animation: notif-panel-in 440ms 60ms var(--phx-ease) backwards;
}

.notif-panel::before,
.notif-panel::after {
  content: '';
  position: absolute;
  z-index: -1;
  pointer-events: none;
  clip-path: polygon(
    0 0,
    calc(100% - var(--phx-cut)) 0,
    100% var(--phx-cut),
    100% 100%,
    var(--phx-cut-s) 100%,
    0 calc(100% - var(--phx-cut-s))
  );
}

.notif-panel::before {
  inset: 0;
  background:
    linear-gradient(
      225deg,
      var(--phx-accent) 0 calc(var(--phx-cut) * 0.9),
      transparent calc(var(--phx-cut) * 0.9 + 1px)
    ),
    color-mix(in srgb, var(--phx-ink) 16%, transparent);
}

.notif-panel::after {
  inset: 1px;
  background:
    linear-gradient(var(--phx-accent), transparent 70%) left top / 2px 100% no-repeat,
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--phx-accent) 5%, var(--phx-surface)),
      var(--phx-surface) 46%
    );
}

.notif-panel__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
  min-height: 52px;
  padding: var(--yg-space-3) calc(var(--phx-cut) + var(--yg-space-3)) var(--yg-space-3)
    var(--yg-space-5);
  border-bottom: 1px solid var(--phx-line-soft);
}

.notif-panel__code {
  flex: 0 0 auto;
  padding: 2px 6px 1px;
  background: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  color: var(--phx-accent);
  font: 400 13px / 1.1 var(--phx-display);
  letter-spacing: 0.16em;
  clip-path: polygon(0 0, calc(100% - 5px) 0, 100% 5px, 100% 100%, 0 100%);
}

.notif-panel__title {
  min-width: 0;
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
}

.notif-panel__badge {
  padding: 2px 8px 1px;
  background: color-mix(in srgb, var(--phx-accent) 14%, transparent);
  color: var(--phx-accent);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
}

.notif-panel__refresh {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-height: 36px;
  margin-inline-start: auto;
  padding: 0 var(--yg-space-3);
  border: 1px solid var(--phx-line-soft);
  background: transparent;
  color: var(--phx-ink);
  font: inherit;
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
  transition:
    border-color 180ms var(--phx-ease),
    background-color 180ms var(--phx-ease);
}

.notif-panel__refresh:hover:not(:disabled) {
  border-color: var(--phx-accent);
  background: color-mix(in srgb, var(--phx-accent) 8%, transparent);
}

.notif-panel__refresh:disabled {
  opacity: 0.55;
  cursor: default;
}

.notif-panel__refresh:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: 2px;
}

.notif-panel__body {
  padding: var(--yg-space-4) var(--yg-space-5) var(--yg-space-5);
}

/* ---------- 骨架 / 空状态 ---------- */
.notif-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
}

.notif-skeleton__text {
  width: 100%;
  height: var(--yg-space-4);
  background: linear-gradient(
      90deg,
      var(--phx-sunken) 0 35%,
      color-mix(in srgb, var(--phx-accent) 12%, var(--phx-sunken)) 50%,
      var(--phx-sunken) 65%
    )
    0 0 / 220% 100%;
  clip-path: polygon(0 0, 100% 0, calc(100% - 8px) 100%, 0 100%);
  animation: notif-scan 1.4s linear infinite;
}

.notif-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-3);
  padding: var(--yg-space-9) var(--yg-space-6);
  text-align: center;
}

.notif-empty__icon {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  background: color-mix(in srgb, var(--phx-accent) 12%, transparent);
  color: var(--phx-accent);
  font-size: 26px;
  clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%);
}

.notif-empty__title {
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-medium);
}

.notif-empty__desc {
  margin: 0;
  color: var(--phx-muted);
  font-size: var(--yg-font-size-sm);
}

.notif-empty__actions {
  display: flex;
  gap: var(--yg-space-3);
  margin-top: var(--yg-space-2);
}

/* ---------- 通知列表 ---------- */
.notif-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.notif-item {
  animation: notif-row-in 360ms calc(var(--phx-n, 0) * 40ms + 160ms) var(--phx-ease) backwards;
}

.notif-item + .notif-item {
  border-top: 1px solid var(--phx-line-soft);
}

.notif-item__head {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2) var(--yg-space-3);
  width: 100%;
  padding: var(--yg-space-3) var(--yg-space-2) var(--yg-space-3) var(--yg-space-4);
  border: 0;
  background: transparent;
  color: var(--phx-ink);
  font: inherit;
  text-align: start;
  cursor: pointer;
  transition:
    background-color 180ms var(--phx-ease),
    transform 260ms var(--phx-spring);
}

.notif-item__head::before {
  content: '';
  position: absolute;
  top: var(--yg-space-2);
  bottom: var(--yg-space-2);
  left: 0;
  width: 2px;
  background: var(--phx-accent);
  transform: scaleY(0);
  transition: transform 260ms var(--phx-spring);
}

.notif-item__head:hover {
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 10%, transparent) 0 45%,
    transparent 80%
  );
  transform: translateX(3px);
}

.notif-item__head:hover::before,
.notif-item__head[aria-expanded='true']::before {
  transform: scaleY(1);
}

.notif-item__head:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: -2px;
}

/* 未读角标：强调色六边形（clip-path，无图片） */
.notif-item__badge {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  background: var(--yg-border-default);
  clip-path: polygon(
    25% 0,
    75% 0,
    100% 50%,
    75% 100%,
    25% 100%,
    0 50%
  );
  transition:
    background-color 200ms var(--phx-ease),
    box-shadow 200ms var(--phx-ease);
}

.notif-item__badge.is-unread {
  background: var(--phx-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--phx-accent) 22%, transparent);
}

.notif-item__title {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
  overflow-wrap: anywhere;
}

.notif-item.is-unread .notif-item__title {
  color: var(--phx-accent);
}

.notif-item__meta {
  flex: 0 0 auto;
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-sm);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.notif-item__state {
  flex: 0 0 auto;
  padding: 2px 8px 1px;
  background: var(--phx-sunken);
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
}

.notif-item.is-unread .notif-item__state {
  background: color-mix(in srgb, var(--phx-accent) 14%, transparent);
  color: var(--phx-accent);
}

.notif-item__body {
  margin: 0 0 var(--yg-space-4) var(--yg-space-4);
  padding: var(--yg-space-1) var(--yg-space-3) 0 var(--yg-space-4);
  border-left: 1px solid var(--phx-line);
  animation: notif-open 300ms var(--phx-ease);
}

.notif-item__content {
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
  overflow-wrap: anywhere;
}

.notif-item__content :deep(p:last-child) {
  margin-bottom: 0;
}

.notif-item__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
  margin-top: var(--yg-space-3);
}

.notif-btn {
  min-height: 36px;
  padding: 0 var(--yg-space-4);
  border: 1px solid var(--phx-line-soft);
  background: transparent;
  color: var(--phx-ink);
  font: inherit;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  cursor: pointer;
  transition:
    border-color 160ms var(--phx-ease),
    background-color 160ms var(--phx-ease);
}

.notif-btn:hover:not(:disabled) {
  border-color: var(--phx-accent);
  background: color-mix(in srgb, var(--phx-accent) 8%, transparent);
}

.notif-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.notif-btn--primary {
  color: var(--yg-bg-page);
  background: var(--phx-accent);
  border-color: transparent;
  font-weight: var(--yg-font-weight-semibold);
}

.notif-btn--primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--phx-accent) 84%, #000);
}

.notif-btn:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: 2px;
}

/* ---------- 动效 ---------- */
@keyframes notif-head-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 22px, 0 100%, 0 100%);
  }
  to {
    opacity: 1;
    clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  }
}

@keyframes notif-panel-in {
  from {
    opacity: 0;
    transform: translateX(-10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes notif-slide-in {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes notif-slide-in-r {
  from {
    opacity: 0;
    transform: translateX(18px) skewX(-8deg);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes notif-row-in {
  from {
    opacity: 0;
    clip-path: inset(-4px 100% -4px -12px);
  }
  to {
    opacity: 1;
    clip-path: inset(-4px -4px -4px -12px);
  }
}

@keyframes notif-open {
  from {
    opacity: 0;
    clip-path: inset(0 0 100% 0);
  }
  to {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

@keyframes notif-scan {
  from {
    background-position: 120% 0;
  }
  to {
    background-position: -120% 0;
  }
}

/* ---------- 响应式 ---------- */
@media (max-width: 959px) {
  .notif-page {
    padding: var(--yg-space-5);
  }
}

@media (max-width: 599px) {
  .notif-page {
    gap: var(--yg-space-4);
    padding: var(--yg-space-4);
  }

  .phx-head {
    padding: var(--yg-space-5) var(--yg-space-4) var(--yg-space-4);
  }

  .phx-head__index {
    font-size: 56px;
  }

  .phx-head::after {
    right: 12px;
    width: 72px;
  }

  .notif-panel__head {
    padding-left: var(--yg-space-4);
  }

  .notif-panel__body {
    padding: var(--yg-space-4);
  }

  .notif-panel__refresh {
    width: 100%;
    margin-inline-start: 0;
    justify-content: center;
  }

  .notif-item__meta {
    width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .phx-head,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .notif-panel,
  .notif-item {
    animation: none;
  }

  .notif-skeleton__text {
    animation: none;
  }

  .notif-item__head,
  .notif-item__head::before,
  .notif-item__badge {
    transition: none;
  }

  .notif-item__head:hover {
    transform: none;
  }
}
</style>

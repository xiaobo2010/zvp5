<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchHealth, fetchVersion } from '@/api/canary'

const router = useRouter()

const refreshing = ref(false)
const lastRefreshed = ref(new Date())

const serverStatus = ref({
  status: 'online',
  uptime: '12天 4小时 32分钟',
  apiVersion: 'v5.0.0',
  maintenance: false,
  database: 'connected',
  workers: 'running',
  queueSize: 3
})

function goBack() {
  router.back()
}

async function loadServerStatus() {
  const [health, version] = await Promise.allSettled([fetchHealth(), fetchVersion()])
  serverStatus.value.status = health.status === 'fulfilled' ? 'online' : 'offline'
  if (version.status === 'fulfilled' && version.value) {
    serverStatus.value.apiVersion = version.value
  }
  lastRefreshed.value = new Date()
}

async function handleRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadServerStatus()
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  void loadServerStatus()
})

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}
</script>

<template>
  <div class="server">
    <div class="server__container">
      <header class="server__header surface-enter" style="--i: 0">
        <button
          type="button"
          class="server__back"
          aria-label="返回"
          @click="goBack"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 20 20"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div>
          <h1 class="server__title">服务器管理</h1>
          <p class="server__subtitle">查看服务器状态和运行信息</p>
        </div>
      </header>

      <div class="server__grid">
        <div class="status-card surface-enter" style="--i: 1">
          <div class="status-card__header">
            <h2 class="status-card__title">服务状态</h2>
            <span
              class="status-badge"
              :class="`status-badge--${serverStatus.status}`"
            >
              {{ serverStatus.status === 'online' ? '在线' : '离线' }}
            </span>
          </div>
          <div class="status-card__body">
            <div class="stat-row">
              <span class="stat-row__label">运行时间</span>
              <span class="stat-row__value">{{ serverStatus.uptime }}</span>
            </div>
          </div>
        </div>

        <div class="status-card surface-enter" style="--i: 2">
          <div class="status-card__header">
            <h2 class="status-card__title">API 版本</h2>
          </div>
          <div class="status-card__body">
            <div class="stat-row">
              <span class="stat-row__label">当前版本</span>
              <span class="stat-row__value stat-row__value--mono">
                {{ serverStatus.apiVersion }}
              </span>
            </div>
          </div>
        </div>

        <div class="status-card surface-enter" style="--i: 3">
          <div class="status-card__header">
            <h2 class="status-card__title">维护模式</h2>
            <span
              class="status-badge"
              :class="serverStatus.maintenance ? 'status-badge--warning' : 'status-badge--success'"
            >
              {{ serverStatus.maintenance ? '已启用' : '未启用' }}
            </span>
          </div>
          <div class="status-card__body">
            <p class="status-card__text">
              {{ serverStatus.maintenance ? '系统当前处于维护模式' : '系统正常运行' }}
            </p>
          </div>
        </div>

        <div class="status-card surface-enter" style="--i: 4">
          <div class="status-card__header">
            <h2 class="status-card__title">数据库</h2>
            <span
              class="status-badge"
              :class="`status-badge--${serverStatus.database === 'connected' ? 'success' : 'error'}`"
            >
              {{ serverStatus.database === 'connected' ? '已连接' : '未连接' }}
            </span>
          </div>
          <div class="status-card__body">
            <div class="stat-row">
              <span class="stat-row__label">连接池</span>
              <span class="stat-row__value">活跃</span>
            </div>
          </div>
        </div>

        <div class="status-card surface-enter" style="--i: 5">
          <div class="status-card__header">
            <h2 class="status-card__title">Worker 队列</h2>
            <span class="status-badge status-badge--info">
              {{ serverStatus.queueSize }} 个任务
            </span>
          </div>
          <div class="status-card__body">
            <div class="stat-row">
              <span class="stat-row__label">状态</span>
              <span class="stat-row__value">{{ serverStatus.workers }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="info-panel surface-enter" style="--i: 6">
        <div class="info-panel__header">
          <h2 class="info-panel__title">运行信息</h2>
          <button
            type="button"
            class="refresh-button"
            :disabled="refreshing"
            :aria-label="refreshing ? '刷新中' : '刷新'"
            @click="handleRefresh"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 18 18"
              fill="none"
              aria-hidden="true"
              :class="{ 'refresh-icon--spinning': refreshing }"
            >
              <path
                d="M2 9C2 5.134 5.134 2 9 2C11.214 2 13.169 3.068 14.38 4.7M16 9C16 12.866 12.866 16 9 16C6.786 16 4.831 14.932 3.62 13.3"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                d="M11 4.5L14.5 4.5L14.5 1"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                d="M7 13.5L3.5 13.5L3.5 17"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
        <div class="info-panel__body">
          <div class="info-row">
            <span class="info-row__key">环境</span>
            <span class="info-row__value">Production</span>
          </div>
          <div class="info-row">
            <span class="info-row__key">主机</span>
            <span class="info-row__value">zvms-prod-01</span>
          </div>
          <div class="info-row">
            <span class="info-row__key">端口</span>
            <span class="info-row__value">8000</span>
          </div>
          <div class="info-row">
            <span class="info-row__key">进程 ID</span>
            <span class="info-row__value">24586</span>
          </div>
          <div class="info-row info-row--last">
            <span class="info-row__key">最后刷新</span>
            <span class="info-row__value">{{ formatTime(lastRefreshed) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.server {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.server__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.server__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}

.server__back {
  position: relative;
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 2px 8px rgba(18, 48, 82, 0.08);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.server__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.server__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.server__back:active {
  transform: scale(0.95);
}

.server__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.server__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.server__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.server__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-5);
  margin-bottom: var(--space-7);
}

/* Status cards */
.status-card {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-5);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
}

.status-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.4;
  pointer-events: none;
}

.status-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  position: relative;
  z-index: 1;
}

.status-card__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
}

.status-card__body {
  position: relative;
  z-index: 1;
}

.status-card__text {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.status-badge {
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-pill);
  white-space: nowrap;
}

.status-badge--online,
.status-badge--success {
  background: var(--tone-green-bg);
  color: var(--tone-green);
}

.status-badge--warning {
  background: var(--tone-amber-bg);
  color: var(--tone-amber);
}

.status-badge--error {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.status-badge--info {
  background: var(--tone-blue-bg);
  color: var(--tone-blue);
}

.stat-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.stat-row__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

.stat-row__value {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
}

.stat-row__value--mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  letter-spacing: -0.01em;
}

/* Info panel */
.info-panel {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
}

.info-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.4;
  pointer-events: none;
}

.info-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
  position: relative;
  z-index: 1;
}

.info-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
}

.info-panel__body {
  position: relative;
  z-index: 1;
}

.refresh-button {
  width: 40px;
  height: 40px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.12) 0%,
    rgba(0, 113, 227, 0.08) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-brand);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 2px 8px rgba(0, 113, 227, 0.12);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.refresh-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  opacity: 0.3;
  pointer-events: none;
}

.refresh-button:hover {
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 12px rgba(0, 113, 227, 0.18);
}

.refresh-button:active {
  transform: scale(0.95);
}

.refresh-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.refresh-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.refresh-icon--spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  gap: var(--space-4);
}

.info-row--last {
  border-bottom: none;
}

.info-row__key {
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
}

.info-row__value {
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
  text-align: right;
}

@media (prefers-reduced-motion: reduce) {
  .server__back,
  .refresh-button {
    transition: none;
  }

  .server__back:hover,
  .refresh-button:hover {
    transform: none;
  }

  .refresh-icon--spinning {
    animation: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .status-card,
  .info-panel {
    background: rgba(255, 255, 255, 0.92);
  }

  .server__back,
  .refresh-button {
    background: rgba(255, 255, 255, 0.92);
  }
}
</style>

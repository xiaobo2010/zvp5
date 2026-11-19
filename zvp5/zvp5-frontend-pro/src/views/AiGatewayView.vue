<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAiHealth } from '@/api/canary'

const router = useRouter()

const refreshing = ref(false)
const lastRefreshed = ref(new Date())
const gatewayError = ref('')

const gatewayStatus = ref({
  online: true,
  concurrency: 8,
  maxConcurrency: 20,
  rateLimit: '100/min',
  requestsToday: 2847
})

const providers = [
  { name: 'OpenAI', status: 'active', models: 3 },
  { name: 'Anthropic', status: 'active', models: 2 },
  { name: 'Google', status: 'inactive', models: 1 }
]

const recentCalls = [
  { id: '1', model: 'gpt-4', status: 'success', duration: '1.2s', timestamp: '14:32:18' },
  { id: '2', model: 'claude-3', status: 'success', duration: '0.8s', timestamp: '14:31:45' },
  { id: '3', model: 'gpt-4', status: 'error', duration: '2.1s', timestamp: '14:30:12' },
  { id: '4', model: 'claude-3', status: 'success', duration: '1.5s', timestamp: '14:28:36' },
  { id: '5', model: 'gemini-pro', status: 'success', duration: '0.9s', timestamp: '14:27:52' }
]

function goBack() {
  router.back()
}

async function loadGatewayStatus() {
  try {
    const health = await fetchAiHealth()
    gatewayStatus.value.online = health.enabled
    gatewayStatus.value.maxConcurrency = health.max_concurrency
    const limit = Object.values(health.rate_limit_per_minute ?? {})[0]
    if (typeof limit === 'number') gatewayStatus.value.rateLimit = `${limit}/min`
    gatewayError.value = ''
  } catch (reason) {
    gatewayStatus.value.online = false
    gatewayError.value = reason instanceof Error ? reason.message : '网关状态读取失败。'
  }
  lastRefreshed.value = new Date()
}

async function handleRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await loadGatewayStatus()
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  void loadGatewayStatus()
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
  <div class="ai-gateway">
    <div class="ai-gateway__container">
      <header class="ai-gateway__header surface-enter" style="--i: 0">
        <button
          type="button"
          class="ai-gateway__back"
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
          <h1 class="ai-gateway__title">AI 网关</h1>
          <p class="ai-gateway__subtitle">查看 AI 服务状态和调用记录</p>
        </div>
      </header>

      <div class="status-panel surface-enter" style="--i: 1">
        <div class="status-panel__header">
          <h2 class="status-panel__title">网关状态</h2>
          <div class="status-panel__actions">
            <span
              class="status-badge"
              :class="gatewayStatus.online ? 'status-badge--success' : 'status-badge--error'"
            >
              {{ gatewayStatus.online ? '在线' : '离线' }}
            </span>
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
        </div>
        <div class="status-panel__body">
          <p v-if="gatewayError" class="gateway-error" role="alert">{{ gatewayError }}</p>
          <div class="status-grid">
            <div class="status-item">
              <div class="status-item__label">并发请求</div>
              <div class="status-item__value">
                {{ gatewayStatus.concurrency }} / {{ gatewayStatus.maxConcurrency }}
              </div>
            </div>
            <div class="status-item">
              <div class="status-item__label">速率限制</div>
              <div class="status-item__value">{{ gatewayStatus.rateLimit }}</div>
            </div>
            <div class="status-item">
              <div class="status-item__label">今日请求</div>
              <div class="status-item__value">{{ gatewayStatus.requestsToday }}</div>
            </div>
            <div class="status-item">
              <div class="status-item__label">最后刷新</div>
              <div class="status-item__value">{{ formatTime(lastRefreshed) }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="content-grid">
        <section class="providers-section surface-enter" style="--i: 2">
          <h2 class="section-title">提供商</h2>
          <div class="providers-list">
            <div
              v-for="provider in providers"
              :key="provider.name"
              class="provider-row"
            >
              <div class="provider-row__name">{{ provider.name }}</div>
              <div class="provider-row__meta">
                <span class="provider-row__models">{{ provider.models }} 个模型</span>
                <span
                  class="status-dot"
                  :class="`status-dot--${provider.status}`"
                  :aria-label="provider.status === 'active' ? '活跃' : '不活跃'"
                />
              </div>
            </div>
          </div>
        </section>

        <section class="calls-section surface-enter" style="--i: 3">
          <h2 class="section-title">最近调用</h2>
          <div class="calls-list">
            <div
              v-for="call in recentCalls"
              :key="call.id"
              class="call-row"
            >
              <div class="call-row__main">
                <div class="call-row__model">{{ call.model }}</div>
                <div class="call-row__meta">
                  <span class="call-row__duration">{{ call.duration }}</span>
                  <span class="call-row__time">{{ call.timestamp }}</span>
                </div>
              </div>
              <span
                class="call-status"
                :class="`call-status--${call.status}`"
              >
                {{ call.status === 'success' ? '成功' : '失败' }}
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-gateway {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.ai-gateway__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.ai-gateway__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}

.ai-gateway__back {
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

.ai-gateway__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.ai-gateway__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.ai-gateway__back:active {
  transform: scale(0.95);
}

.ai-gateway__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.ai-gateway__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.ai-gateway__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

/* Status panel */
.status-panel {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
  box-shadow: var(--glass-shadow);
  margin-bottom: var(--space-7);
  isolation: isolate;
}

.status-panel::before {
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

.status-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  position: relative;
  z-index: 1;
}

.status-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
}

.status-panel__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.status-panel__body {
  position: relative;
  z-index: 1;
}

.gateway-error {
  margin: 0 0 var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--tone-rose-bg);
  border: 1px solid rgba(255, 59, 48, 0.24);
  border-radius: var(--radius-md);
  color: rgb(153, 27, 27);
  font-size: var(--font-size-callout);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-5);
}

.status-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.status-item__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

.status-item__value {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
}

.status-badge {
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-pill);
  white-space: nowrap;
}

.status-badge--success {
  background: var(--tone-green-bg);
  color: var(--tone-green);
}

.status-badge--error {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
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

/* Content grid */
.content-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-6);
}

@media (min-width: 1024px) {
  .content-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.section-title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-4);
  line-height: var(--line-height-snug);
}

/* Providers section */
.providers-section {
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

.providers-section::before {
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

.providers-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  position: relative;
  z-index: 1;
}

.provider-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--color-border-hairline);
}

.provider-row:last-child {
  border-bottom: none;
}

.provider-row__name {
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
}

.provider-row__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.provider-row__models {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot--active {
  background: var(--tone-green);
  box-shadow: 0 0 8px rgba(52, 199, 89, 0.4);
}

.status-dot--inactive {
  background: var(--color-text-tertiary);
}

/* Calls section */
.calls-section {
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

.calls-section::before {
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

.calls-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  position: relative;
  z-index: 1;
}

.call-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  gap: var(--space-4);
}

.call-row:last-child {
  border-bottom: none;
}

.call-row__main {
  flex: 1;
  min-width: 0;
}

.call-row__model {
  font-size: var(--font-size-body);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-1);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  letter-spacing: -0.01em;
}

.call-row__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.call-row__duration {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
}

.call-status {
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-pill);
  white-space: nowrap;
  flex-shrink: 0;
}

.call-status--success {
  background: var(--tone-green-bg);
  color: var(--tone-green);
}

.call-status--error {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

@media (prefers-reduced-motion: reduce) {
  .ai-gateway__back,
  .refresh-button {
    transition: none;
  }

  .ai-gateway__back:hover,
  .refresh-button:hover {
    transform: none;
  }

  .refresh-icon--spinning {
    animation: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .status-panel,
  .providers-section,
  .calls-section {
    background: rgba(255, 255, 255, 0.92);
  }

  .ai-gateway__back,
  .refresh-button {
    background: rgba(255, 255, 255, 0.92);
  }
}
</style>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const greetingVisible = ref(false)
const sectionsVisible = ref<Record<string, boolean>>({
  identity: false,
  hours: false,
  work: false
})

let observer: IntersectionObserver | null = null
let greetingTimeout: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  greetingTimeout = setTimeout(() => {
    greetingVisible.value = true
  }, 100)

  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.getAttribute('data-section')
          if (id) {
            sectionsVisible.value[id] = true
          }
        }
      })
    },
    { threshold: 0.1, rootMargin: '-60px' }
  )

  document.querySelectorAll('[data-section]').forEach((el) => {
    observer?.observe(el)
  })
})

onUnmounted(() => {
  if (observer) {
    observer.disconnect()
    observer = null
  }
  if (greetingTimeout) {
    clearTimeout(greetingTimeout)
    greetingTimeout = null
  }
})
</script>

<template>
  <div id="home" class="home">
    <!-- Greeting -->
    <section class="greeting" :class="{ visible: greetingVisible }">
      <h1 class="greeting__title">下午好，知远。</h1>
      <p class="greeting__date">2026年11月18日 星期三</p>
    </section>

    <!-- Identity + Total Hours -->
    <section
      id="profile"
      class="identity-row"
      data-section="identity"
      :class="{ visible: sectionsVisible.identity }"
    >
      <!-- Personal Identity Card -->
      <article class="identity-card">
        <div class="identity-card__header">
          <div class="identity-card__avatar">
            <span class="identity-card__avatar-text">林</span>
          </div>
          <div class="identity-card__info">
            <h2 class="identity-card__name">林知远</h2>
            <p class="identity-card__number">学号 20240117</p>
          </div>
        </div>
        <div class="identity-card__meta">
          <div class="identity-card__meta-item">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="M9 15C12.3137 15 15 12.3137 15 9C15 5.68629 12.3137 3 9 3C5.68629 3 3 5.68629 3 9C3 12.3137 5.68629 15 9 15Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
              <path d="M9 6V9L11 11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span>校图书馆志愿者协会</span>
          </div>
          <div class="identity-card__meta-item">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <circle cx="9" cy="6" r="2.5" stroke="currentColor" stroke-width="1.5"/>
              <path d="M4 14C4 11.7909 5.79086 10 8 10H10C12.2091 10 14 11.7909 14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <span>学生 / 志愿者</span>
          </div>
        </div>
        <button class="identity-card__action glass-button">
          编辑资料
        </button>
      </article>

      <!-- Total Hours Anchor -->
      <article class="total-hours">
        <p class="total-hours__label">累计记录</p>
        <p class="total-hours__value">54.5<span class="total-hours__unit">h</span></p>
        <p class="total-hours__note">包含三类时长</p>
      </article>
    </section>

    <!-- Hours Panel -->
    <section
      id="hours"
      class="hours-panel glass-panel"
      data-section="hours"
      :class="{ visible: sectionsVisible.hours }"
    >
      <div class="hours-panel__header">
        <h2 class="hours-panel__title">义工时长</h2>
        <div class="hours-panel__actions">
          <button class="glass-button glass-button--compact">明细</button>
          <button class="glass-button glass-button--compact">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M14 8C14 11.3137 11.3137 14 8 14C4.68629 14 2 11.3137 2 8C2 4.68629 4.68629 2 8 2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M14 2V6H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            刷新
          </button>
        </div>
      </div>

      <div class="hours-grid">
        <!-- On-campus hours -->
        <div class="hour-category">
          <div class="hour-category__header">
            <h3 class="hour-category__title">校内时长</h3>
            <span class="hour-category__progress hour-category__progress--good">116%</span>
          </div>
          <div class="hour-category__values">
            <div class="hour-category__main">
              <span class="hour-category__actual">28.0</span>
              <span class="hour-category__unit">h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">标准要求</span>
              <span class="hour-category__standard">25h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">折算后</span>
              <span class="hour-category__converted">29.0h <span class="hour-category__bonus">+1</span></span>
            </div>
          </div>
          <div class="hour-category__bar">
            <div class="hour-category__fill" style="--progress: 116%"></div>
          </div>
        </div>

        <!-- Off-campus hours -->
        <div class="hour-category">
          <div class="hour-category__header">
            <h3 class="hour-category__title">校外时长</h3>
            <span class="hour-category__progress hour-category__progress--good">120%</span>
          </div>
          <div class="hour-category__values">
            <div class="hour-category__main">
              <span class="hour-category__actual">17.0</span>
              <span class="hour-category__unit">h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">标准要求</span>
              <span class="hour-category__standard">15h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">折算后</span>
              <span class="hour-category__converted">18.0h <span class="hour-category__bonus">+1</span></span>
            </div>
          </div>
          <div class="hour-category__bar">
            <div class="hour-category__fill" style="--progress: 120%"></div>
          </div>
        </div>

        <!-- Social practice hours -->
        <div class="hour-category">
          <div class="hour-category__header">
            <h3 class="hour-category__title">社会实践</h3>
            <span class="hour-category__progress hour-category__progress--warn">53%</span>
          </div>
          <div class="hour-category__values">
            <div class="hour-category__main">
              <span class="hour-category__actual">9.5</span>
              <span class="hour-category__unit">h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">标准要求</span>
              <span class="hour-category__standard">18h</span>
            </div>
            <div class="hour-category__detail">
              <span class="hour-category__label">折算后</span>
              <span class="hour-category__converted">9.5h</span>
            </div>
          </div>
          <div class="hour-category__bar">
            <div class="hour-category__fill" style="--progress: 53%"></div>
          </div>
        </div>
      </div>

      <p class="hours-panel__note">
        超出标准的校外时长可折算为校内时长（每2小时折算1小时），超出标准的校内时长可折算为校外时长（每3小时折算1小时）
      </p>
    </section>

    <!-- Work Lists -->
    <section
      id="activities"
      class="work-lists"
      data-section="work"
      :class="{ visible: sectionsVisible.work }"
    >
      <!-- Pending activities -->
      <article class="work-list glass-panel">
        <h2 class="work-list__title">待审核活动</h2>
        <div class="work-list__items">
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">社区环保宣传</p>
              <p class="work-item__meta">社区服务中心 · 11月24日</p>
            </div>
            <span class="work-item__status work-item__status--pending">待审核</span>
          </a>
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">敬老院陪伴服务</p>
              <p class="work-item__meta">阳光敬老院 · 11月25日</p>
            </div>
            <span class="work-item__status work-item__status--pending">待审核</span>
          </a>
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">校园开放日引导</p>
              <p class="work-item__meta">学生发展中心 · 11月28日</p>
            </div>
            <span class="work-item__status work-item__status--pending">待审核</span>
          </a>
        </div>
      </article>

      <!-- Recent records -->
      <article class="work-list glass-panel">
        <h2 class="work-list__title">最近记录</h2>
        <div class="work-list__items">
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">市图书馆导览服务</p>
              <p class="work-item__meta">11月10日 · 2.0h</p>
            </div>
            <span class="work-item__status work-item__status--confirm">待确认</span>
          </a>
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">校园图书整理</p>
              <p class="work-item__meta">11月03日 · 3.0h</p>
            </div>
            <span class="work-item__status work-item__status--done">已确认</span>
          </a>
          <a href="#" class="work-item">
            <div class="work-item__content">
              <p class="work-item__name">社区义务家教</p>
              <p class="work-item__meta">10月26日 · 2.5h</p>
            </div>
            <span class="work-item__status work-item__status--done">已确认</span>
          </a>
        </div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: var(--space-8) clamp(20px, 4vw, 40px) var(--space-10);
  max-width: 1200px;
  margin: 0 auto;
}

/* Greeting */
.greeting {
  margin-bottom: var(--space-7);
  opacity: 0;
  transform: translateY(12px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.greeting.visible {
  opacity: 1;
  transform: translateY(0);
}

.greeting__title {
  font-size: var(--font-size-greeting);
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-2) 0;
  text-wrap: balance;
}

.greeting__date {
  font-size: var(--font-size-body-large);
  color: var(--color-text-secondary);
  margin: 0;
}

/* Identity Row */
.identity-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-8);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 100ms;
}

.identity-row.visible {
  opacity: 1;
  transform: translateY(0);
}

/* Identity Card */
.identity-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
}

.identity-card__header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.identity-card__avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--color-brand);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.identity-card__avatar-text {
  font-size: 28px;
  font-weight: 500;
  color: white;
}

.identity-card__info {
  flex: 1;
}

.identity-card__name {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
}

.identity-card__number {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

.identity-card__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

.identity-card__meta-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}

.identity-card__meta-item svg {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

.identity-card__action {
  align-self: flex-start;
  width: auto;
  min-width: 120px;
}

/* Total Hours */
.total-hours {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.total-hours__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: 0 0 var(--space-3) 0;
}

.total-hours__value {
  font-size: var(--font-size-stats);
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-2) 0;
}

.total-hours__unit {
  font-size: 0.5em;
  font-weight: 400;
  color: var(--color-text-secondary);
  margin-left: var(--space-1);
}

.total-hours__note {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Glass Panel Base */
.glass-panel {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
  padding: var(--space-6);
}

.glass-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

/* A single diagonal refraction band makes the glass legible on a light canvas. */
.glass-panel::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(
    115deg,
    rgba(255, 255, 255, 0) 18%,
    rgba(255, 255, 255, 0.38) 38%,
    rgba(255, 255, 255, 0.04) 50%,
    rgba(255, 255, 255, 0) 64%
  );
  pointer-events: none;
  opacity: 0.55;
  z-index: 0;
}

.glass-panel > * {
  position: relative;
  z-index: 1;
}

/* Glass Button */
.glass-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
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
  font-size: var(--font-size-body);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-spring);
  overflow: hidden;
  isolation: isolate;
  min-height: 44px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(255, 255, 255, 0.3),
    0 2px 8px rgba(18, 48, 82, 0.06);
}

.glass-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  pointer-events: none;
  opacity: 0.3;
}

.glass-button::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-sheen);
  border-radius: var(--radius-md);
  opacity: 0.22;
  transform: translateX(-38%);
  transition: all var(--duration-base) var(--ease-smooth);
  pointer-events: none;
}

.glass-button:hover {
  transform: scale(1.01);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.96),
    inset 0 -1px 0 rgba(255, 255, 255, 0.36),
    0 6px 18px rgba(18, 48, 82, 0.09);
}

.glass-button:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.glass-button:active {
  transform: scale(0.98);
}

.glass-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
}

.glass-button--compact {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-callout);
  min-height: 36px;
}

.glass-button--compact svg {
  width: 14px;
  height: 14px;
}

/* Hours Panel */
.hours-panel {
  margin-bottom: var(--space-8);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 200ms;
}

.hours-panel.visible {
  opacity: 1;
  transform: translateY(0);
}

.hours-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  position: relative;
  z-index: 1;
}

.hours-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.hours-panel__actions {
  display: flex;
  gap: var(--space-2);
}

.hours-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-5);
  position: relative;
  z-index: 1;
}

.hour-category {
  padding: var(--space-5);
  background: rgba(255, 255, 255, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: var(--radius-lg);
}

.hour-category__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.hour-category__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.hour-category__progress {
  font-size: var(--font-size-caption);
  font-weight: 600;
  padding: 4px var(--space-2);
  border-radius: var(--radius-xs);
}

.hour-category__progress--good {
  background: rgba(52, 199, 89, 0.15);
  color: #34c759;
}

.hour-category__progress--warn {
  background: rgba(255, 159, 10, 0.15);
  color: #ff9f0a;
}

.hour-category__values {
  margin-bottom: var(--space-4);
}

.hour-category__main {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.hour-category__actual {
  font-size: clamp(40px, 5vw, 48px);
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.02em;
  color: var(--color-text-primary);
}

.hour-category__unit {
  font-size: var(--font-size-body);
  font-weight: 400;
  color: var(--color-text-secondary);
}

.hour-category__detail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-callout);
  margin-bottom: var(--space-1);
}

.hour-category__label {
  color: var(--color-text-tertiary);
}

.hour-category__standard,
.hour-category__converted {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.hour-category__bonus {
  color: #34c759;
  font-weight: 600;
  margin-left: var(--space-1);
}

.hour-category__bar {
  height: 6px;
  background: rgba(0, 0, 0, 0.08);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

.hour-category__fill {
  height: 100%;
  width: var(--progress);
  max-width: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-pill);
  transition: width var(--duration-base) var(--ease-smooth);
}

.hours-panel__note {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
  line-height: var(--line-height-relaxed);
  position: relative;
  z-index: 1;
}

/* Work Lists */
.work-lists {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-10);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 300ms;
}

.work-lists.visible {
  opacity: 1;
  transform: translateY(0);
}

.work-list__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-5) 0;
  position: relative;
  z-index: 1;
}

.work-list__items {
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 1;
}

.work-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  text-decoration: none;
  transition: all var(--duration-fast) var(--ease-smooth);
  min-height: 68px;
}

.work-item:last-child {
  border-bottom: none;
}

.work-item:hover {
  background: rgba(255, 255, 255, 0.4);
  transform: translateX(2px);
}

.work-item:active {
  transform: scale(0.99);
}

.work-item:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
  border-radius: var(--radius-sm);
}

.work-item__content {
  flex: 1;
  min-width: 0;
}

.work-item__name {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
}

.work-item__meta {
  overflow: hidden;
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-item__status {
  font-size: var(--font-size-caption);
  font-weight: 500;
  padding: 4px var(--space-3);
  border-radius: var(--radius-pill);
  white-space: nowrap;
  flex-shrink: 0;
}

.work-item__status--pending {
  background: rgba(255, 159, 10, 0.15);
  color: #ff9f0a;
}

.work-item__status--confirm {
  background: rgba(0, 113, 227, 0.15);
  color: var(--color-brand);
}

.work-item__status--done {
  background: rgba(52, 199, 89, 0.15);
  color: #34c759;
}

/* Tablet */
@media (min-width: 768px) {
  .identity-row {
    grid-template-columns: 7fr 5fr;
  }

  .hours-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .work-lists {
    grid-template-columns: 1fr 1fr;
  }
}

/* Desktop */
@media (min-width: 1024px) {
  .home {
    padding-top: var(--space-10);
  }

  .greeting {
    margin-bottom: var(--space-8);
  }

  .identity-row {
    margin-bottom: var(--space-10);
  }

  .hours-panel {
    margin-bottom: var(--space-10);
  }

  .hours-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .work-lists {
    grid-template-columns: 7fr 5fr;
  }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .greeting,
  .identity-row,
  .hours-panel,
  .work-lists {
    opacity: 1;
    transform: none;
  }

  .glass-button::after {
    display: none;
  }
}
</style>

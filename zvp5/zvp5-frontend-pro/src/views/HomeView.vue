<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const greetingVisible = ref(false)
const sectionsVisible = ref<Record<string, boolean>>({
  pass: false,
  metrics: false,
  bento: false
})

let observer: IntersectionObserver | null = null
let greetingTimeout: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  // Stagger entrance
  greetingTimeout = setTimeout(() => {
    greetingVisible.value = true
  }, 100)

  // Scroll reveal for sections
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
      <p class="greeting__date">2026年11月17日 星期二</p>
      <p class="greeting__context">这周你有 2 场志愿安排</p>
    </section>

    <!-- Focal next activity pass -->
    <section
      id="activity-detail"
      class="pass"
      data-section="pass"
      :class="{ visible: sectionsVisible.pass }"
    >
      <div class="pass__inner">
        <div class="pass__content">
          <div class="pass__header">
            <h2 class="pass__title">城市书房周末导读</h2>
            <span class="pass__badge">已报名</span>
          </div>
          <div class="pass__meta">
            <div class="pass__meta-item">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <path d="M9 15C12.3137 15 15 12.3137 15 9C15 5.68629 12.3137 3 9 3C5.68629 3 3 5.68629 3 9C3 12.3137 5.68629 15 9 15Z" stroke="currentColor" stroke-width="1.5"/>
                <path d="M9 3V9L12 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              <span>市图书馆三层</span>
            </div>
            <div class="pass__meta-item">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                <circle cx="9" cy="6" r="2.5" stroke="currentColor" stroke-width="1.5"/>
                <path d="M4 14C4 11.7909 5.79086 10 8 10H10C12.2091 10 14 11.7909 14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
              <span>导读志愿者</span>
            </div>
          </div>
          <a href="#activity-detail" class="pass__action">查看详情</a>
        </div>

        <div class="pass__date">
          <div class="pass__date-day">22</div>
          <div class="pass__date-month">NOV</div>
          <div class="pass__date-meta">
            <div class="pass__date-weekday">周六</div>
            <div class="pass__date-time">14:00–16:00</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Metrics strip -->
    <section
      class="metrics"
      data-section="metrics"
      :class="{ visible: sectionsVisible.metrics }"
    >
      <div class="metric">
        <p class="metric__value">42.5h</p>
        <p class="metric__label">累计时长</p>
      </div>
      <div class="metric__divider" />
      <div class="metric">
        <p class="metric__value">6.5h</p>
        <p class="metric__label">本月新增</p>
      </div>
      <div class="metric__divider" />
      <div class="metric">
        <p class="metric__value">8</p>
        <p class="metric__label">已完成</p>
      </div>
      <div class="metric__divider" />
      <div class="metric">
        <p class="metric__value">1</p>
        <p class="metric__label">待确认</p>
      </div>
    </section>

    <!-- Bento grid -->
    <section
      class="bento"
      data-section="bento"
      :class="{ visible: sectionsVisible.bento }"
    >
      <!-- Hours chart -->
      <article id="hours" class="bento-card bento-card--hours">
        <h3 class="bento-card__title">我的时长</h3>
        <div class="hours-chart">
          <div class="hours-chart__bar" style="--height: 45">
            <span class="hours-chart__value">4.5h</span>
            <span class="hours-chart__label">9月</span>
          </div>
          <div class="hours-chart__bar" style="--height: 72">
            <span class="hours-chart__value">7.2h</span>
            <span class="hours-chart__label">10月</span>
          </div>
          <div class="hours-chart__bar hours-chart__bar--current" style="--height: 65">
            <span class="hours-chart__value">6.5h</span>
            <span class="hours-chart__label">11月</span>
          </div>
        </div>
        <p class="bento-card__footer">本月比上月减少 0.7 小时</p>
      </article>

      <!-- Pending -->
      <article class="bento-card bento-card--pending">
        <h3 class="bento-card__title">待确认</h3>
        <a id="pending-item" href="#pending-item" class="pending-item">
          <div class="pending-item__content">
            <p class="pending-item__title">市图书馆导览服务</p>
            <p class="pending-item__meta">2.0h · 11月10日</p>
          </div>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M7 5L12 10L7 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </a>
      </article>

      <!-- Recommended activities -->
      <article id="activities" class="bento-card bento-card--activities">
        <h3 class="bento-card__title">推荐活动</h3>
        <div class="activity-list">
          <a id="activity-1" href="#activity-1" class="activity-mini">
            <div class="activity-mini__header">
              <p class="activity-mini__title">社区环保宣传</p>
              <div class="activity-mini__badge">招募中</div>
            </div>
            <p class="activity-mini__meta">11月24日 · 社区中心</p>
          </a>
          <a id="activity-2" href="#activity-2" class="activity-mini">
            <div class="activity-mini__header">
              <p class="activity-mini__title">敬老院陪伴服务</p>
              <div class="activity-mini__badge">招募中</div>
            </div>
            <p class="activity-mini__meta">11月25日 · 阳光敬老院</p>
          </a>
        </div>
      </article>

      <!-- Quick actions -->
      <article class="bento-card bento-card--actions">
        <h3 class="bento-card__title">快捷操作</h3>
        <div class="quick-actions">
          <a href="#activities" class="quick-action">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.5"/>
              <path d="M13.5 13.5L17 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <span>找活动</span>
          </a>
          <a href="#hours" class="quick-action">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <path d="M7 3V5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M13 3V5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M3 8H17" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <span>记录时长</span>
          </a>
          <a href="#activity-detail" class="quick-action">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect x="4" y="3" width="12" height="14" rx="1.5" stroke="currentColor" stroke-width="1.5"/>
              <path d="M7 7H13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M7 10H13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M7 13H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <span>我的证书</span>
          </a>
          <a href="#home-footer" class="quick-action">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <path d="M3 7L10 11L17 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span>联系组织</span>
          </a>
        </div>
      </article>
    </section>

    <!-- Quiet footer -->
    <footer id="home-footer" class="home-footer">
      <div class="home-footer__links">
        <span>帮助中心</span>
        <span class="home-footer__dot">·</span>
        <span>隐私政策</span>
        <span class="home-footer__dot">·</span>
        <span>服务条款</span>
      </div>
      <p class="home-footer__copy">© 2026 ZVMS. 保留所有权利。</p>
    </footer>
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  padding: var(--space-8) clamp(20px, 4vw, 40px) var(--space-10);
  max-width: 1180px;
  margin: 0 auto;
}

/* Greeting */
.greeting {
  margin-bottom: var(--space-8);
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
  margin: 0 0 var(--space-1) 0;
}

.greeting__context {
  font-size: var(--font-size-body);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Focal activity pass */
.pass {
  margin-bottom: var(--space-8);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 100ms;
}

.pass.visible {
  opacity: 1;
  transform: translateY(0);
}

.pass__inner {
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow:
    var(--glass-shadow),
    0 8px 32px rgb(0 113 227 / 0.08),
    0 0 0 1px rgb(255 255 255 / 0.1) inset;
}

/* Light bloom effect */
.pass__inner::before {
  content: '';
  position: absolute;
  inset: -1px;
  background: linear-gradient(135deg,
    rgba(255, 255, 255, 0.6) 0%,
    rgba(255, 255, 255, 0) 50%
  );
  border-radius: var(--radius-xl);
  pointer-events: none;
  opacity: 0.8;
}

.pass__content {
  padding: var(--space-6);
  flex: 1;
}

.pass__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.pass__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  margin: 0;
  flex: 1;
  text-wrap: balance;
}

.pass__badge {
  display: inline-flex;
  padding: var(--space-1) var(--space-3);
  background: rgb(0 113 227 / 0.1);
  color: var(--color-brand);
  font-size: var(--font-size-caption);
  font-weight: 500;
  border-radius: var(--radius-pill);
  white-space: nowrap;
  flex-shrink: 0;
}

.pass__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-6);
}

.pass__meta-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}

.pass__meta-item svg {
  flex-shrink: 0;
}

.pass__action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-3) var(--space-6);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-body);
  font-weight: 500;
  text-decoration: none;
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-spring);
  min-height: 44px;
}

.pass__action:hover {
  background: var(--color-brand-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgb(0 113 227 / 0.2);
}

.pass__action:active {
  transform: scale(0.98);
}

.pass__action:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
}

.pass__date {
  position: relative;
  padding: var(--space-6);
  background: linear-gradient(135deg,
    rgb(0 113 227 / 0.08) 0%,
    rgb(0 113 227 / 0.03) 100%
  );
  border-top: 1px dashed rgb(0 0 0 / 0.08);
  display: flex;
  align-items: center;
  gap: var(--space-5);
}

.pass__date::before {
  content: '';
  position: absolute;
  top: -6px;
  left: -6px;
  width: 12px;
  height: 12px;
  background: var(--color-bg-canvas);
  border-radius: 50%;
}

.pass__date::after {
  content: '';
  position: absolute;
  top: -6px;
  right: -6px;
  width: 12px;
  height: 12px;
  background: var(--color-bg-canvas);
  border-radius: 50%;
}

.pass__date-day {
  font-size: clamp(48px, 6vw, 64px);
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.03em;
  color: var(--color-text-primary);
}

.pass__date-month {
  font-size: var(--font-size-headline);
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--color-text-secondary);
  text-transform: uppercase;
}

.pass__date-meta {
  margin-left: auto;
  text-align: right;
}

.pass__date-weekday {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  margin-bottom: var(--space-1);
}

.pass__date-time {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

/* Metrics strip */
.metrics {
  display: flex;
  align-items: stretch;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-8);
  overflow: hidden;
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 200ms;
}

.metrics.visible {
  opacity: 1;
  transform: translateY(0);
}

.metric {
  flex: 1;
  padding: var(--space-5);
  text-align: center;
}

.metric__divider {
  width: 1px;
  background: var(--color-border-hairline);
  align-self: stretch;
  margin: var(--space-4) 0;
}

.metric__value {
  font-size: var(--font-size-stats);
  font-weight: 600;
  line-height: var(--line-height-tight);
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
  letter-spacing: -0.02em;
}

.metric__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Bento grid */
.bento {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-10);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
  transition-delay: 300ms;
}

.bento.visible {
  opacity: 1;
  transform: translateY(0);
}

.bento-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.bento-card:hover {
  box-shadow: var(--shadow-sm);
}

.bento-card__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-5) 0;
}

.bento-card__footer {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  margin: var(--space-4) 0 0 0;
}

/* Hours chart */
.hours-chart {
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  height: 140px;
  gap: var(--space-3);
  margin: var(--space-5) 0;
}

.hours-chart__bar {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
}

.hours-chart__bar::before {
  content: '';
  width: 100%;
  max-width: 48px;
  height: calc(var(--height) / 100 * 140px);
  background: var(--color-border-moderate);
  border-radius: var(--radius-xs) var(--radius-xs) 0 0;
  transition: all var(--duration-base) var(--ease-smooth);
}

.hours-chart__bar--current::before {
  background: var(--color-brand);
}

.hours-chart__value {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  order: -1;
}

.hours-chart__label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

/* Pending item */
.pending-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4);
  background: var(--color-bg-canvas);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  text-decoration: none;
  transition: all var(--duration-fast) var(--ease-spring);
  min-height: 72px;
}

.pending-item:hover {
  background: var(--color-bg-sunken);
  transform: translateX(2px);
}

.pending-item:active {
  transform: scale(0.99);
}

.pending-item:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.pending-item__content {
  flex: 1;
}

.pending-item__title {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0 0 var(--space-1) 0;
}

.pending-item__meta {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

.pending-item svg {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

/* Activity list */
.activity-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.activity-mini {
  display: block;
  padding: var(--space-4);
  background: var(--color-bg-canvas);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  text-decoration: none;
  transition: all var(--duration-fast) var(--ease-spring);
}

.activity-mini:hover {
  background: var(--color-bg-sunken);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.activity-mini:active {
  transform: scale(0.99);
}

.activity-mini:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.activity-mini__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.activity-mini__title {
  font-size: var(--font-size-body);
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0;
  flex: 1;
}

.activity-mini__badge {
  display: inline-flex;
  padding: 2px var(--space-2);
  background: rgb(52 199 89 / 0.1);
  color: #34c759;
  font-size: 11px;
  font-weight: 500;
  border-radius: var(--radius-xs);
  white-space: nowrap;
  flex-shrink: 0;
}

.activity-mini__meta {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  margin: 0;
}

/* Quick actions */
.quick-actions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
}

.quick-action {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-4);
  background: var(--color-bg-canvas);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  font-size: var(--font-size-callout);
  font-weight: 500;
  text-decoration: none;
  transition: all var(--duration-fast) var(--ease-spring);
  min-height: 80px;
}

.quick-action:hover {
  background: var(--color-bg-sunken);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.quick-action:active {
  transform: scale(0.98);
}

.quick-action:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.quick-action svg {
  color: var(--color-text-secondary);
}

/* Footer */
.home-footer {
  text-align: center;
  padding: var(--space-8) 0 var(--space-6);
  border-top: 1px solid var(--color-border-hairline);
  margin-top: var(--space-10);
}

.home-footer__links {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.home-footer__links a {
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--font-size-callout);
  transition: color var(--duration-fast) var(--ease-smooth);
}

.home-footer__links a:hover {
  color: var(--color-text-primary);
}

.home-footer__links a:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
  border-radius: var(--radius-xs);
}

.home-footer__dot {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
}

.home-footer__copy {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin: 0;
}

/* Responsive: tablet */
@media (max-width: 599px) {
  .metrics {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric {
    padding: var(--space-4);
  }

  .metric__divider {
    display: none;
  }

  .metric:nth-child(1),
  .metric:nth-child(3) {
    border-right: 1px solid var(--color-border-hairline);
  }

  .metric:nth-child(1),
  .metric:nth-child(3) {
    border-bottom: 1px solid var(--color-border-hairline);
  }

  .metric__label {
    font-size: var(--font-size-caption);
  }
}

@media (min-width: 768px) {
  .pass__inner {
    flex-direction: row;
  }

  .pass__content {
    flex: 7;
  }

  .pass__date {
    flex: 5;
    flex-direction: column;
    justify-content: center;
    align-items: flex-start;
    border-top: none;
    border-left: 1px dashed rgb(0 0 0 / 0.08);
  }

  .pass__date::before {
    top: -6px;
    left: -6px;
  }

  .pass__date::after {
    bottom: -6px;
    left: -6px;
    top: auto;
    right: auto;
  }

  .pass__date-meta {
    margin-left: 0;
    margin-top: var(--space-4);
    text-align: left;
  }

  .bento {
    grid-template-columns: repeat(2, 1fr);
  }

  .bento-card--hours {
    grid-column: span 2;
  }

  .bento-card--activities {
    grid-column: span 2;
  }
}

/* Responsive: desktop */
@media (min-width: 1024px) {
  .home {
    padding-top: var(--space-10);
  }

  .greeting {
    margin-bottom: var(--space-10);
  }

  .pass {
    margin-bottom: var(--space-10);
  }

  .metrics {
    margin-bottom: var(--space-10);
  }

  .bento {
    grid-template-columns: repeat(12, 1fr);
  }

  .bento-card--hours {
    grid-column: span 7;
  }

  .bento-card--pending {
    grid-column: span 5;
  }

  .bento-card--activities {
    grid-column: span 8;
  }

  .bento-card--actions {
    grid-column: span 4;
  }
}

/* Responsive: large desktop */
@media (min-width: 1280px) {
  .pass__content {
    padding: var(--space-7);
  }

  .pass__date {
    padding: var(--space-7);
  }
}
</style>

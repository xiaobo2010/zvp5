<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const heroVisible = ref(false)
const sectionsVisible = ref<Record<string, boolean>>({
  proof: false,
  discover: false,
  track: false,
  connect: false,
  activities: false,
  cta: false
})

let observer: IntersectionObserver | null = null
let heroTimeout: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  // Stagger hero elements
  heroTimeout = setTimeout(() => {
    heroVisible.value = true
  }, 100)

  // Simple scroll reveal
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
    { threshold: 0.1, rootMargin: '-80px' }
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
  if (heroTimeout) {
    clearTimeout(heroTimeout)
    heroTimeout = null
  }
})
</script>

<template>
  <div class="home">
    <!-- Hero -->
    <section id="top" class="hero">
      <div class="hero__content" :class="{ visible: heroVisible }">
        <p class="hero__eyebrow">ZVMS · 志愿管理系统</p>
        <h1 class="hero__title">
          <span>让每一次志愿</span><span>都被看见</span>
        </h1>
        <p class="hero__subtitle">
          一个更简单、更透明的校园志愿管理平台。发现活动、记录时长、连接组织。
        </p>
        <div class="hero__actions">
          <a href="#start" class="hero__cta hero__cta--primary">开始使用</a>
          <a href="#about" class="hero__cta hero__cta--secondary">了解更多</a>
        </div>
      </div>

      <!-- Glass product visual -->
      <div class="hero__visual" :class="{ visible: heroVisible }">
        <div class="mock-card">
          <div class="mock-card__header">
            <div class="mock-card__tag">志愿活动</div>
            <div class="mock-card__status">招募中</div>
          </div>
          <h3 class="mock-card__title">图书馆整理与导览服务</h3>
          <p class="mock-card__meta">校图书馆 · 本周六 14:00-16:00</p>
          <div class="mock-card__footer">
            <div class="mock-card__info">
              <span class="mock-card__label">时长</span>
              <span class="mock-card__value">2.0h</span>
            </div>
            <div class="mock-card__info">
              <span class="mock-card__label">人数</span>
              <span class="mock-card__value">8/10</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Proof strip -->
    <section
      id="impact"
      class="proof"
      data-section="proof"
      :class="{ visible: sectionsVisible.proof }"
    >
      <div class="proof__grid">
        <div class="proof__item">
          <div class="proof__number">10,000+</div>
          <div class="proof__label">累计志愿时长</div>
        </div>
        <div class="proof__item">
          <div class="proof__number">500+</div>
          <div class="proof__label">活动已完成</div>
        </div>
        <div class="proof__item">
          <div class="proof__number">50+</div>
          <div class="proof__label">合作组织</div>
        </div>
        <div class="proof__item">
          <div class="proof__number">2,000+</div>
          <div class="proof__label">活跃志愿者</div>
        </div>
      </div>
    </section>

    <!-- Product section: Discover -->
    <section
      id="activities"
      class="feature"
      data-section="discover"
      :class="{ visible: sectionsVisible.discover }"
    >
      <div class="feature__content">
        <h2 class="feature__title">发现适合你的<br />志愿活动</h2>
        <p class="feature__description">
          按时间、类别、组织筛选，找到真正感兴趣的服务机会。实时查看名额、获取详细信息，一键报名。
        </p>
        <ul class="feature__list">
          <li>智能推荐匹配你的兴趣方向</li>
          <li>实时更新活动状态与名额</li>
          <li>快速筛选与多维度搜索</li>
        </ul>
      </div>
      <div class="feature__visual">
        <div class="mock-list">
          <div class="mock-list__filters">
            <div class="mock-filter">全部类别</div>
            <div class="mock-filter mock-filter--active">本周</div>
            <div class="mock-filter">校内</div>
          </div>
          <div class="mock-list__item">
            <div class="mock-list__badge">教育支持</div>
            <div class="mock-list__title">义务家教辅导</div>
            <div class="mock-list__meta">学生发展中心 · 明天</div>
          </div>
          <div class="mock-list__item">
            <div class="mock-list__badge">环保公益</div>
            <div class="mock-list__title">校园环境清洁</div>
            <div class="mock-list__meta">后勤部 · 本周六</div>
          </div>
          <div class="mock-list__item mock-list__item--muted">
            <div class="mock-list__badge">文化传播</div>
            <div class="mock-list__title">博物馆讲解服务</div>
            <div class="mock-list__meta">市博物馆 · 下周</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Product section: Track -->
    <section
      class="feature feature--reverse"
      data-section="track"
      :class="{ visible: sectionsVisible.track }"
    >
      <div class="feature__content">
        <h2 class="feature__title">准确记录<br />每一分钟</h2>
        <p class="feature__description">
          自动生成时长记录，组织方在线确认，全流程透明可追溯。随时查看个人志愿档案与统计分析。
        </p>
        <ul class="feature__list">
          <li>自动计算与智能验证</li>
          <li>多级审核保证真实性</li>
          <li>个人档案永久留存</li>
        </ul>
      </div>
      <div class="feature__visual">
        <div class="mock-record">
          <div class="mock-record__header">
            <div class="mock-record__title">志愿时长</div>
            <div class="mock-record__total">24.5 <span>小时</span></div>
          </div>
          <div class="mock-record__chart">
            <div class="mock-bar" style="height: 60%"></div>
            <div class="mock-bar" style="height: 85%"></div>
            <div class="mock-bar" style="height: 45%"></div>
            <div class="mock-bar" style="height: 100%"></div>
            <div class="mock-bar" style="height: 70%"></div>
          </div>
          <div class="mock-record__item">
            <div class="mock-record__dot"></div>
            <div class="mock-record__info">
              <div class="mock-record__name">图书馆整理服务</div>
              <div class="mock-record__time">2.0h · 已确认</div>
            </div>
          </div>
          <div class="mock-record__item">
            <div class="mock-record__dot mock-record__dot--pending"></div>
            <div class="mock-record__info">
              <div class="mock-record__name">社区义务家教</div>
              <div class="mock-record__time">3.0h · 待审核</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Product section: Connect -->
    <section
      id="organizations"
      class="feature"
      data-section="connect"
      :class="{ visible: sectionsVisible.connect }"
    >
      <div class="feature__content">
        <h2 class="feature__title">连接志同道合<br />的组织与伙伴</h2>
        <p class="feature__description">
          关注感兴趣的组织，了解他们的使命与影响力。加入社群，与其他志愿者交流经验，共同成长。
        </p>
        <ul class="feature__list">
          <li>认证组织信息公开透明</li>
          <li>活动历史与评价可查询</li>
          <li>社群互动与经验分享</li>
        </ul>
      </div>
      <div class="feature__visual">
        <div class="mock-org">
          <div class="mock-org__avatar">图</div>
          <h3 class="mock-org__name">校图书馆志愿者协会</h3>
          <p class="mock-org__desc">致力于推广阅读文化，提供图书整理、导览讲解等服务</p>
          <div class="mock-org__stats">
            <div class="mock-org__stat">
              <div class="mock-org__stat-value">128</div>
              <div class="mock-org__stat-label">志愿者</div>
            </div>
            <div class="mock-org__stat">
              <div class="mock-org__stat-value">45</div>
              <div class="mock-org__stat-label">活动</div>
            </div>
            <div class="mock-org__stat">
              <div class="mock-org__stat-value">890h</div>
              <div class="mock-org__stat-label">总时长</div>
            </div>
          </div>
          <div class="mock-org__action">关注组织</div>
        </div>
      </div>
    </section>

    <!-- Activities showcase -->
    <section
      class="activities"
      data-section="activities"
      :class="{ visible: sectionsVisible.activities }"
    >
      <div class="activities__header">
        <h2 class="activities__title">近期热门活动</h2>
        <p class="activities__subtitle">加入这些活动，开始你的志愿之旅</p>
      </div>
      <div class="activities__grid">
        <article class="activity-card">
          <div class="activity-card__tag">教育支持</div>
          <h3 class="activity-card__title">乡村小学支教计划</h3>
          <p class="activity-card__desc">为乡村学生提供课业辅导与素质拓展课程</p>
          <div class="activity-card__meta">
            <span>学生发展中心</span>
            <span>本周日 9:00</span>
          </div>
          <div class="activity-card__footer">
            <div class="activity-card__info">
              <span class="activity-card__label">时长</span>
              <span class="activity-card__value">4.0h</span>
            </div>
            <div class="activity-card__info">
              <span class="activity-card__label">名额</span>
              <span class="activity-card__value">12/15</span>
            </div>
          </div>
        </article>

        <article class="activity-card">
          <div class="activity-card__tag">环保公益</div>
          <h3 class="activity-card__title">湿地保护与科普宣传</h3>
          <p class="activity-card__desc">参与湿地环境监测与生态知识普及活动</p>
          <div class="activity-card__meta">
            <span>环保协会</span>
            <span>下周六 8:00</span>
          </div>
          <div class="activity-card__footer">
            <div class="activity-card__info">
              <span class="activity-card__label">时长</span>
              <span class="activity-card__value">5.0h</span>
            </div>
            <div class="activity-card__info">
              <span class="activity-card__label">名额</span>
              <span class="activity-card__value">8/20</span>
            </div>
          </div>
        </article>

        <article class="activity-card">
          <div class="activity-card__tag">文化传播</div>
          <h3 class="activity-card__title">城市历史文化导览</h3>
          <p class="activity-card__desc">为游客提供历史建筑讲解与文化体验服务</p>
          <div class="activity-card__meta">
            <span>市文旅局</span>
            <span>周末全天</span>
          </div>
          <div class="activity-card__footer">
            <div class="activity-card__info">
              <span class="activity-card__label">时长</span>
              <span class="activity-card__value">3.0h</span>
            </div>
            <div class="activity-card__info">
              <span class="activity-card__label">名额</span>
              <span class="activity-card__value">5/10</span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- Closing CTA -->
    <section
      id="start"
      class="cta"
      data-section="cta"
      :class="{ visible: sectionsVisible.cta }"
    >
      <h2 class="cta__title">准备好开始了吗？</h2>
      <p class="cta__subtitle">加入 ZVMS，让你的志愿服务更有价值</p>
      <a href="#start" class="cta__button">立即开始</a>
    </section>

    <!-- Footer -->
    <footer id="about" class="footer">
      <div class="footer__content">
        <div class="footer__brand">
          <div class="footer__logo">
            <span class="footer__mark">Z</span>
            <span>ZVMS</span>
          </div>
          <p class="footer__tagline">让每一次志愿都被看见</p>
        </div>

        <div class="footer__links">
          <div class="footer__column">
            <h3 class="footer__heading">产品</h3>
            <a href="#activities" class="footer__link">志愿活动</a>
            <a href="#impact" class="footer__link">志愿时长</a>
            <a href="#organizations" class="footer__link">组织管理</a>
          </div>

          <div class="footer__column">
            <h3 class="footer__heading">支持</h3>
            <a href="#about" class="footer__link">关于我们</a>
            <a href="#about" class="footer__link">使用指南</a>
            <a href="#about" class="footer__link">常见问题</a>
          </div>

          <div class="footer__column">
            <h3 class="footer__heading">联系</h3>
            <a href="#about" class="footer__link">联系我们</a>
            <a href="#about" class="footer__link">加入我们</a>
            <a href="#about" class="footer__link">反馈建议</a>
          </div>
        </div>
      </div>

      <div class="footer__bottom">
        <p class="footer__copyright">© 2026 ZVMS. 志愿管理系统</p>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.home {
  overflow-x: hidden;
}

/* Hero Section */
.hero {
  position: relative;
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-10);
  min-height: calc(100vh - 64px);
  padding: var(--space-12) var(--content-padding);
  align-items: center;
  justify-items: center;
}

.hero__content {
  max-width: 640px;
  text-align: center;
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.hero__content.visible {
  opacity: 1;
  transform: translateY(0);
}

.hero__eyebrow {
  color: var(--color-brand);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: var(--letter-spacing-wide);
  text-transform: uppercase;
  margin-bottom: var(--space-5);
}

.hero__title {
  font-size: var(--font-size-hero);
  font-weight: 700;
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-6);
  text-wrap: balance;
}

/* Keep each phrase intact so a Chinese word never splits across lines. */
.hero__title span {
  display: inline-block;
}

.hero__subtitle {
  font-size: var(--font-size-headline);
  line-height: var(--line-height-relaxed);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-8);
}

.hero__actions {
  display: flex;
  gap: var(--space-4);
  justify-content: center;
  flex-wrap: wrap;
}

.hero__cta {
  padding: var(--space-4) var(--space-7);
  border-radius: var(--radius-md);
  font-size: var(--font-size-body);
  font-weight: 600;
  transition: all var(--duration-base) var(--ease-spring);
  min-width: 140px;
  text-align: center;
}

.hero__cta--primary {
  background: var(--color-brand);
  color: white;
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.3);
}

.hero__cta--primary:hover {
  background: var(--color-brand-hover);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgb(0 113 227 / 0.4);
}

.hero__cta--primary:active {
  transform: translateY(0) scale(0.98);
}

.hero__cta--secondary {
  background: var(--color-bg-elevated);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border-moderate);
}

.hero__cta--secondary:hover {
  background: white;
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.hero__cta--secondary:active {
  transform: translateY(0) scale(0.98);
}

.hero__visual {
  width: 100%;
  max-width: 480px;
  opacity: 0;
  transform: translateY(30px) scale(0.95);
  transition: all var(--duration-slow) var(--ease-spring) 0.2s;
}

.hero__visual.visible {
  opacity: 1;
  transform: translateY(0) scale(1);
}

/* Mock activity card - liquid glass */
.mock-card {
  padding: var(--space-7);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
  box-shadow:
    var(--shadow-xl),
    0 0 60px rgb(0 113 227 / 0.08),
    0 0 120px rgb(0 113 227 / 0.04);
  position: relative;
}

.mock-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 5%;
  right: 5%;
  height: 2px;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(255, 255, 255, 0.6),
    var(--glass-border),
    rgba(255, 255, 255, 0.6),
    transparent
  );
  filter: blur(0.5px);
}

.mock-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-5);
}

.mock-card__tag {
  padding: var(--space-2) var(--space-3);
  background: rgb(0 113 227 / 0.1);
  color: var(--color-brand);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-sm);
}

.mock-card__status {
  padding: var(--space-2) var(--space-3);
  background: rgb(52 199 89 / 0.1);
  color: rgb(48 176 80);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-sm);
}

.mock-card__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
  letter-spacing: var(--letter-spacing-normal);
}

.mock-card__meta {
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  margin-bottom: var(--space-6);
}

.mock-card__footer {
  display: flex;
  gap: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px solid var(--color-border-subtle);
}

.mock-card__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.mock-card__label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.mock-card__value {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
}

/* Proof strip */
.proof {
  padding: var(--space-10) var(--content-padding);
  opacity: 0;
  transform: translateY(20px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.proof.visible {
  opacity: 1;
  transform: translateY(0);
}

.proof__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-6);
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.proof__item {
  padding: var(--space-7);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  text-align: center;
  transition: all var(--duration-base) var(--ease-spring);
}

.proof__item:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  background: white;
}

.proof__number {
  font-size: var(--font-size-title-2);
  font-weight: 700;
  color: var(--color-brand);
  letter-spacing: var(--letter-spacing-tight);
  margin-bottom: var(--space-2);
}

.proof__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
}

/* Feature sections */
.feature {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-10);
  padding: var(--section-gap) var(--content-padding);
  max-width: 1400px;
  margin: 0 auto;
  opacity: 0;
  transform: translateY(30px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.feature.visible {
  opacity: 1;
  transform: translateY(0);
}

.feature__content {
  max-width: 540px;
}

.feature__title {
  font-size: var(--font-size-title-1);
  font-weight: 700;
  letter-spacing: var(--letter-spacing-tight);
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  margin-bottom: var(--space-6);
}

.feature__description {
  font-size: var(--font-size-headline);
  line-height: var(--line-height-relaxed);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-7);
}

.feature__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.feature__list li {
  padding-left: var(--space-6);
  font-size: var(--font-size-body);
  color: var(--color-text-secondary);
  position: relative;
}

.feature__list li::before {
  content: '✓';
  position: absolute;
  left: 0;
  color: var(--color-brand);
  font-weight: 700;
}

.feature__visual {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Mock list UI */
.mock-list {
  width: 100%;
  max-width: 480px;
  padding: var(--space-6);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.mock-list__filters {
  display: flex;
  gap: var(--space-3);
  margin-bottom: var(--space-6);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.mock-filter {
  padding: var(--space-2) var(--space-4);
  background: white;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.mock-filter--active {
  background: var(--color-brand);
  color: white;
  border-color: var(--color-brand);
}

.mock-list__item {
  padding: var(--space-4);
  margin-bottom: var(--space-3);
  background: white;
  border-radius: var(--radius-sm);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.mock-list__item:hover {
  transform: translateX(4px);
  box-shadow: var(--shadow-sm);
}

.mock-list__item--muted {
  opacity: 0.5;
}

.mock-list__badge {
  display: inline-block;
  padding: 2px var(--space-2);
  background: rgb(0 113 227 / 0.1);
  color: var(--color-brand);
  font-size: 11px;
  font-weight: 600;
  border-radius: var(--radius-xs);
  margin-bottom: var(--space-2);
}

.mock-list__title {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-1);
}

.mock-list__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

/* Mock record UI */
.mock-record {
  width: 100%;
  max-width: 480px;
  padding: var(--space-7);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
  position: relative;
}

.mock-record::before {
  content: '';
  position: absolute;
  top: 0;
  left: 5%;
  right: 5%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-border), transparent);
}

.mock-record__header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: var(--space-6);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.mock-record__title {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
  font-weight: 600;
}

.mock-record__total {
  font-size: var(--font-size-title-2);
  font-weight: 700;
  color: var(--color-brand);
  letter-spacing: var(--letter-spacing-tight);
}

.mock-record__total span {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.mock-record__chart {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
  height: 80px;
  margin-bottom: var(--space-7);
  padding: var(--space-4);
  background: rgb(0 113 227 / 0.04);
  border-radius: var(--radius-sm);
}

.mock-bar {
  flex: 1;
  background: linear-gradient(to top, var(--color-brand), rgb(0 113 227 / 0.6));
  border-radius: var(--radius-xs);
  min-height: 20px;
  transition: all var(--duration-base) var(--ease-spring);
}

.mock-bar:hover {
  background: var(--color-brand);
  transform: scaleY(1.05);
}

.mock-record__item {
  display: flex;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  background: white;
  border-radius: var(--radius-sm);
}

.mock-record__dot {
  width: 12px;
  height: 12px;
  margin-top: 4px;
  background: rgb(52 199 89);
  border-radius: 50%;
  flex-shrink: 0;
}

.mock-record__dot--pending {
  background: rgb(255 159 10);
}

.mock-record__info {
  flex: 1;
}

.mock-record__name {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: 2px;
}

.mock-record__time {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

/* Mock organization UI */
.mock-org {
  width: 100%;
  max-width: 400px;
  padding: var(--space-8);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  text-align: center;
}

.mock-org__avatar {
  width: 80px;
  height: 80px;
  margin: 0 auto var(--space-5);
  display: grid;
  place-items: center;
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-title-3);
  font-weight: 700;
  border-radius: var(--radius-lg);
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.3);
}

.mock-org__name {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
  letter-spacing: var(--letter-spacing-normal);
}

.mock-org__desc {
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-7);
}

.mock-org__stats {
  display: flex;
  justify-content: space-around;
  margin-bottom: var(--space-7);
  padding: var(--space-5) 0;
  border-top: 1px solid var(--color-border-subtle);
  border-bottom: 1px solid var(--color-border-subtle);
}

.mock-org__stat-value {
  font-size: var(--font-size-headline);
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: var(--space-1);
}

.mock-org__stat-label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.mock-org__action {
  width: 100%;
  padding: var(--space-4);
  background: var(--color-bg-elevated);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
  font-weight: 600;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-moderate);
  text-align: center;
}

/* Activities section */
.activities {
  padding: var(--section-gap) var(--content-padding);
  background: var(--color-bg-sunken);
  opacity: 0;
  transform: translateY(30px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.activities.visible {
  opacity: 1;
  transform: translateY(0);
}

.activities__header {
  text-align: center;
  max-width: 640px;
  margin: 0 auto var(--space-10);
}

.activities__title {
  font-size: var(--font-size-title-1);
  font-weight: 700;
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-4);
}

.activities__subtitle {
  font-size: var(--font-size-headline);
  color: var(--color-text-secondary);
}

.activities__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--space-7);
  max-width: 1200px;
  margin: 0 auto;
}

.activity-card {
  padding: var(--space-7);
  background: white;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--duration-base) var(--ease-spring);
}

.activity-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.activity-card__tag {
  display: inline-block;
  padding: var(--space-2) var(--space-3);
  background: rgb(0 113 227 / 0.1);
  color: var(--color-brand);
  font-size: var(--font-size-caption);
  font-weight: 600;
  border-radius: var(--radius-sm);
  margin-bottom: var(--space-4);
}

.activity-card__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
  letter-spacing: var(--letter-spacing-normal);
}

.activity-card__desc {
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-5);
}

.activity-card__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  margin-bottom: var(--space-5);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.activity-card__footer {
  display: flex;
  gap: var(--space-6);
}

.activity-card__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.activity-card__label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.activity-card__value {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
}

/* CTA section */
.cta {
  padding: var(--section-gap) var(--content-padding);
  text-align: center;
  opacity: 0;
  transform: translateY(30px);
  transition: all var(--duration-slow) var(--ease-smooth);
}

.cta.visible {
  opacity: 1;
  transform: translateY(0);
}

.cta__title {
  font-size: var(--font-size-title-1);
  font-weight: 700;
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-4);
}

.cta__subtitle {
  font-size: var(--font-size-headline);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-8);
}

.cta__button {
  display: inline-block;
  padding: var(--space-5) var(--space-9);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-headline);
  font-weight: 600;
  border-radius: var(--radius-md);
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.3);
  transition: all var(--duration-base) var(--ease-spring);
}

.cta__button:hover {
  background: var(--color-brand-hover);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgb(0 113 227 / 0.4);
}

.cta__button:active {
  transform: translateY(0) scale(0.98);
}

/* Footer */
.footer {
  padding: var(--space-11) var(--content-padding) var(--space-8);
  background: var(--color-bg-elevated);
  border-top: 1px solid var(--color-border-subtle);
}

.footer__content {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-8);
  max-width: var(--content-max-width);
  margin: 0 auto var(--space-10);
}

.footer__brand {
  max-width: 320px;
}

.footer__logo {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
}

.footer__mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  background: var(--color-brand);
  color: white;
  font-weight: 700;
  border-radius: var(--radius-sm);
}

.footer__tagline {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
}

.footer__links {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-7);
}

.footer__column {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.footer__heading {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.footer__link {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  transition: color var(--duration-fast) var(--ease-smooth);
}

.footer__link:hover {
  color: var(--color-brand);
}

.footer__bottom {
  padding-top: var(--space-7);
  border-top: 1px solid var(--color-border-subtle);
  text-align: center;
}

.footer__copyright {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

/* Responsive adjustments */
@media (min-width: 768px) {
  .feature {
    grid-template-columns: 1fr 1fr;
    align-items: center;
  }

  .feature--reverse {
    direction: rtl;
  }

  .feature--reverse > * {
    direction: ltr;
  }

  .footer__content {
    grid-template-columns: 1.5fr 2fr;
  }
}

@media (min-width: 1024px) {
  .hero {
    padding: var(--space-12) var(--content-padding);
  }

  .activities__grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (min-width: 1200px) {
  .hero {
    grid-template-columns: 1fr 1fr;
    gap: var(--space-12);
  }

  .hero__content {
    text-align: left;
    justify-self: end;
  }

  .hero__actions {
    justify-content: flex-start;
  }
}
</style>

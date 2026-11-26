<script setup lang="ts">
import { ElCollapse } from 'element-plus/es/components/collapse/index.mjs'
import { ElCollapseItem } from 'element-plus/es/components/collapse/index.mjs'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import { onMounted, ref } from 'vue'
import { staticAssetUrl } from '@/utils/staticAssets'

const { t } = useI18n()
const header = useHeaderStore()
const version = ref('—')

onMounted(async () => {
  const value = await api.version.getVersion()
  if (value) version.value = value
})

header.setHeader(t('nav.about'))
</script>

<template>
  <div class="yg-v2-page phx phx-about">
    <header class="phx-head" style="--phx-i: 0">
      <div class="phx-head__copy">
        <p class="phx-head__eyebrow">About · ZVMS</p>
        <h1 class="phx-head__title">{{ t('nav.about') }}</h1>
        <p class="phx-head__lead">{{ t('about.alternative') }}</p>
      </div>
      <span class="phx-head__index" aria-hidden="true">04</span>
    </header>

    <section class="phx-panel phx-about__board" style="--phx-i: 1" aria-labelledby="about-title">
      <div class="phx-about__mark" aria-hidden="true">
        <img
          class="phx-about__logo"
          :src="staticAssetUrl('pwa-192x192.png')"
          alt=""
        />
      </div>
      <div class="phx-about__copy">
        <p class="phx-about__kicker">Volunteer System</p>
        <h2 id="about-title" class="phx-about__name">镇海中学义工平台</h2>
        <p class="phx-about__bio">{{ t('about.bio') }}</p>
      </div>
      <dl class="phx-about__specs">
        <div class="phx-about__spec phx-about__spec--version">
          <dt>Version</dt>
          <dd>
            <span class="phx-about__spec-zh">版本</span>
            <b>{{ version }}</b>
          </dd>
        </div>
        <div class="phx-about__spec">
          <dt>License</dt>
          <dd>{{ t('about.license') }}</dd>
        </div>
        <div class="phx-about__spec">
          <dt>Since</dt>
          <dd><b>2018</b></dd>
        </div>
      </dl>
    </section>

    <section
      class="phx-panel phx-about__docs"
      style="--phx-i: 2"
      aria-labelledby="about-license-title"
    >
      <div class="phx-panel__head">
        <span class="phx-panel__code" aria-hidden="true">DOC</span>
        <h2 id="about-license-title" class="phx-panel__title">关于本项目</h2>
      </div>
      <div class="phx-panel__body">
        <p class="phx-about__credit">
          © 2018–2026 {{ t('about.footer') }} · {{ t('about.license') }}
        </p>
        <ElCollapse class="phx-about__collapse">
          <ElCollapseItem :title="t('about.repository.license')" name="license">
            <p>{{ t('about.license') }}</p>
          </ElCollapseItem>
          <ElCollapseItem :title="t('about.privacy.title')" name="privacy">
            <p>{{ t('about.privacy.content') }}</p>
          </ElCollapseItem>
        </ElCollapse>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统（页内 scoped，四个普通用户页同一套写法）
   - 直角 + 不对称斜切（右上大切、左下小切），细线边框，offset 硬阴影
   - 颜色只从 --yg-* / --phi-* 派生；浅色模式仅改强调色与硬阴影
   - 动效：页头斜切划入、面板 clip reveal；全部 ≤450ms，减少动态时关闭
   ========================================================================== */
.phx {
  --phx-accent: var(--phi-cyan, var(--yg-color-primary));
  --phx-accent-2: var(--phi-pink, var(--yg-color-danger));
  --phx-on-accent: #041311;
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
  --el-border-radius-base: 0;
  --el-border-radius-small: 0;
  --el-border-radius-round: 0;
  --el-card-border-radius: 0;
  --el-tag-border-radius: 0;
  --el-button-border-radius: 0;
  --el-alert-border-radius-base: 0;
}

html[data-theme='light'] .phx {
  --phx-accent: #007a70;
  --phx-accent-2: #c81e63;
  --phx-on-accent: #ffffff;
  --phx-hard: #101418;
  --el-color-primary: #007a70;
  --yg-color-primary: #007a70;
  --yg-focus-ring-color: #007a70;
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
    linear-gradient(to bottom left, transparent calc(50% - 1px), var(--phx-accent) 50%, transparent calc(50% + 1.5px)) right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(100deg, color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface)), var(--phx-surface) 58%);
  clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  animation: phx-head-in 420ms var(--phx-ease) both;
}

.phx-head::after {
  content: '';
  position: absolute;
  right: 18%;
  bottom: 0;
  width: 120px;
  height: 6px;
  background: repeating-linear-gradient(
    -55deg,
    var(--phx-accent) 0 2px,
    transparent 2px 7px
  );
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
  animation: phx-slide-in 420ms 60ms var(--phx-ease) both;
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
  animation: phx-slide-in 420ms 110ms var(--phx-ease) both;
}

.phx-head__index {
  align-self: start;
  color: transparent;
  font: 400 clamp(64px, 11vw, 112px) / 0.8 var(--phx-display);
  -webkit-text-stroke: 1px color-mix(in srgb, var(--phx-ink) 45%, transparent);
  text-shadow:
    4px 0 color-mix(in srgb, var(--phx-accent-2) 28%, transparent),
    -3px 0 color-mix(in srgb, var(--phx-accent) 32%, transparent);
  user-select: none;
  animation: phx-slide-in-r 450ms 40ms var(--phx-ease) both;
}

/* ---------- 切角面板：右上大切 + 左下小切；边框/底面用两层伪元素画 ---------- */
.phx-panel {
  --phx-cut: 18px;
  --phx-cut-s: 8px;
  position: relative;
  isolation: isolate;
  color: var(--yg-text-secondary);
  filter: drop-shadow(5px 5px 0 var(--phx-hard));
  animation: phx-panel-in 440ms calc(var(--phx-i, 0) * 55ms + 60ms) var(--phx-ease) backwards;
}

.phx-panel::before,
.phx-panel::after {
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

.phx-panel::before {
  inset: 0;
  background:
    linear-gradient(225deg, var(--phx-accent) 0 calc(var(--phx-cut) * 0.9), transparent calc(var(--phx-cut) * 0.9 + 1px)),
    color-mix(in srgb, var(--phx-ink) 16%, transparent);
}

.phx-panel::after {
  inset: 1px;
  background:
    linear-gradient(var(--phx-accent), transparent 70%) left top / 2px 100% no-repeat,
    linear-gradient(135deg, color-mix(in srgb, var(--phx-accent) 5%, var(--phx-surface)), var(--phx-surface) 46%);
}

.phx-panel__head {
  display: flex;
  align-items: center;
  gap: var(--yg-space-3);
  min-height: 52px;
  padding: var(--yg-space-3) calc(var(--phx-cut) + var(--yg-space-3)) var(--yg-space-3) var(--yg-space-5);
  border-bottom: 1px solid var(--phx-line-soft);
}

.phx-panel__code {
  flex: 0 0 auto;
  padding: 2px 6px 1px;
  border: 1px solid var(--phx-line);
  color: var(--phx-accent);
  font: 400 13px / 1.1 var(--phx-display);
  letter-spacing: 0.16em;
}

.phx-panel__title {
  min-width: 0;
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-snug);
}

.phx-panel__body {
  padding: var(--yg-space-4) var(--yg-space-5) var(--yg-space-5);
}

@keyframes phx-head-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 22px, 0 100%, 0 100%);
  }
  to {
    opacity: 1;
    clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  }
}

@keyframes phx-panel-in {
  from {
    opacity: 0;
    transform: translateX(-10px);
    clip-path: polygon(-12px -12px, -12px -12px, calc(-12px - 40px) calc(100% + 12px), -52px calc(100% + 12px));
  }
  to {
    opacity: 1;
    transform: none;
    clip-path: polygon(-12px -12px, calc(100% + 52px) -12px, calc(100% + 12px) calc(100% + 12px), -52px calc(100% + 12px));
  }
}

@keyframes phx-slide-in {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-slide-in-r {
  from {
    opacity: 0;
    transform: translateX(18px) skewX(-8deg);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* ==========================================================================
   关于页：左侧 logo 信息板 + 右下错位文档板
   ========================================================================== */
.yg-v2-page.phx-about {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  align-items: start;
  text-align: left;
}

.phx-about > .phx-head {
  grid-column: 1 / -1;
}

.phx-about__board {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--yg-space-5) var(--yg-space-6);
  align-items: center;
  padding: var(--yg-space-6) calc(var(--phx-cut) + var(--yg-space-5)) var(--yg-space-6) var(--yg-space-6);
}

.phx-about__mark {
  position: relative;
  display: grid;
  place-items: center;
  width: 112px;
  height: 112px;
  background:
    linear-gradient(var(--phx-accent), var(--phx-accent)) left top / 14px 1px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) left top / 1px 14px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) right bottom / 14px 1px no-repeat,
    linear-gradient(var(--phx-accent), var(--phx-accent)) right bottom / 1px 14px no-repeat,
    var(--phx-sunken);
  outline: 1px solid var(--phx-line-soft);
  outline-offset: 5px;
}

.phx-about__mark::after {
  content: '';
  position: absolute;
  inset: -12px;
  background:
    linear-gradient(var(--phx-line), var(--phx-line)) right top / 22px 1px no-repeat,
    linear-gradient(var(--phx-line), var(--phx-line)) right top / 1px 22px no-repeat,
    linear-gradient(var(--phx-line), var(--phx-line)) left bottom / 22px 1px no-repeat,
    linear-gradient(var(--phx-line), var(--phx-line)) left bottom / 1px 22px no-repeat;
  pointer-events: none;
}

.phx-about__logo {
  width: 72px;
  height: 72px;
  clip-path: polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 12px 100%, 0 calc(100% - 12px));
}

.phx-about__copy {
  min-width: 0;
}

.phx-about__kicker {
  margin: 0 0 var(--yg-space-2);
  color: var(--phx-accent);
  font: 400 14px / 1 var(--phx-display);
  letter-spacing: 0.28em;
  text-transform: uppercase;
}

.phx-about__name {
  margin: 0;
  color: var(--phx-ink);
  font-size: clamp(22px, 3.4vw, 30px);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1.15;
}

.phx-about__bio {
  margin: var(--yg-space-2) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.phx-about__specs {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1.3fr) minmax(0, 0.8fr);
  margin: 0;
  border-top: 1px solid var(--phx-line-soft);
}

.phx-about__spec {
  position: relative;
  min-width: 0;
  padding: var(--yg-space-4) var(--yg-space-4) 0;
}

.phx-about__spec + .phx-about__spec {
  border-left: 1px solid var(--phx-line-soft);
}

.phx-about__spec::before {
  content: '';
  position: absolute;
  top: -1px;
  left: 0;
  width: 24px;
  height: 2px;
  background: var(--phx-accent);
}

.phx-about__spec dt {
  margin: 0 0 var(--yg-space-2);
  color: var(--phx-muted);
  font: 400 13px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

.phx-about__spec dd {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
  margin: 0;
  color: var(--phx-ink);
  font-size: var(--yg-font-size-sm);
  overflow-wrap: anywhere;
}

.phx-about__spec dd b {
  color: var(--phx-ink);
  font: 700 22px / 1 var(--phx-hud);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
}

.phx-about__spec--version dd b {
  color: var(--phx-accent);
}

.phx-about__spec-zh {
  color: var(--phx-muted);
  font-size: var(--yg-font-size-xs);
}

.phx-about__docs {
  grid-column: 2 / -1;
  margin-top: calc(var(--yg-space-3) * -1);
}

.phx-about__credit {
  margin: 0 0 var(--yg-space-3);
  color: var(--phx-muted);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
}

.phx-about__collapse {
  --el-collapse-border-color: var(--phx-line-soft);
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  --el-collapse-header-text-color: var(--phx-ink);
  --el-collapse-content-text-color: var(--yg-text-secondary);
  border-top: 1px solid var(--phx-line-soft);
  border-bottom: 1px solid var(--phx-line-soft);
}

.phx-about__collapse :deep(.el-collapse-item__header) {
  position: relative;
  padding-left: var(--yg-space-3);
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  transition:
    color 180ms var(--phx-ease),
    background-color 180ms var(--phx-ease);
}

.phx-about__collapse :deep(.el-collapse-item__header)::before {
  content: '';
  position: absolute;
  top: 30%;
  bottom: 30%;
  left: 0;
  width: 2px;
  background: var(--phx-accent);
  transform: scaleY(0);
  transition: transform 260ms var(--phx-spring);
}

.phx-about__collapse :deep(.el-collapse-item__header:hover),
.phx-about__collapse :deep(.el-collapse-item__header.is-active) {
  color: var(--phx-accent);
  background: linear-gradient(90deg, color-mix(in srgb, var(--phx-accent) 8%, transparent), transparent 70%);
}

.phx-about__collapse :deep(.el-collapse-item__header:hover)::before,
.phx-about__collapse :deep(.el-collapse-item__header.is-active)::before {
  transform: scaleY(1);
}

.phx-about__collapse :deep(.el-collapse-item__content) {
  padding: 0 var(--yg-space-3) var(--yg-space-4);
  line-height: var(--yg-line-height-relaxed);
}

.phx-about__collapse :deep(.el-collapse-item__content p) {
  margin: 0;
}

/* ---------- 响应式 ---------- */
@media (max-width: 959px) {
  .yg-v2-page.phx-about {
    grid-template-columns: minmax(0, 1fr);
  }

  .phx-about__docs {
    grid-column: 1 / -1;
    margin-top: 0;
  }
}

@media (max-width: 599px) {
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

  .phx-panel__head {
    padding-left: var(--yg-space-4);
  }

  .phx-panel__body {
    padding: var(--yg-space-4);
  }

  .phx-about__board {
    grid-template-columns: minmax(0, 1fr);
    padding: var(--yg-space-5) var(--yg-space-4);
  }

  .phx-about__mark {
    width: 88px;
    height: 88px;
    margin: var(--yg-space-2) 0 0 var(--yg-space-2);
  }

  .phx-about__logo {
    width: 56px;
    height: 56px;
  }

  .phx-about__specs {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .phx-about__spec--version {
    grid-column: 1 / -1;
    padding-bottom: var(--yg-space-3);
    border-bottom: 1px solid var(--phx-line-soft);
  }

  .phx-about__spec--version + .phx-about__spec {
    border-left: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .phx-head,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .phx-panel {
    animation: none;
  }

  .phx-about__collapse :deep(.el-collapse-item__header),
  .phx-about__collapse :deep(.el-collapse-item__header)::before {
    transition: none;
  }
}
</style>

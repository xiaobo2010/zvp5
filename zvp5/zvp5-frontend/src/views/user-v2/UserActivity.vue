<script setup lang="ts">
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ref, watch, type Component as VueComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZActivityList } from '@/components'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { User, Group, School } from '@icon-park/vue-next'
import { useUserStore } from '@/stores/user'

const header = useHeaderStore()
const { t } = useI18n()
const user = useUserStore()

header.setHeader(t('nav.activity'))

const route = useRoute()
const router = useRouter()

const tab = ref<'campus' | 'class' | 'mine'>('mine')

watch(
  () => route.params.mode,
  (mode) => {
    const value = Array.isArray(mode) ? mode[0] : mode
    tab.value = value === 'class' || value === 'campus' ? value : 'mine'
    header.setHeader(t('nav.activity'))
  },
  { immediate: true }
)

const panes = [
  {
    value: 'mine',
    color: 'primary',
    icon: User,
    visibility: true
  },
  {
    value: 'class',
    color: 'warning',
    icon: Group,
    visibility: user.position.includes('secretary') || user.position.includes('admin')
  },
  {
    value: 'campus',
    color: 'danger',
    icon: School,
    visibility: user.position.includes('department') || user.position.includes('admin')
  }
] as Array<{
  value: string
  color: 'primary' | 'success' | 'warning' | 'danger' | 'info' | ''
  icon: VueComponent
  visibility?: boolean
}>

function moveTo(type: string) {
  // 等新路由就绪后再切换页签，避免新列表挂载时仍读到旧页签的高页码。
  router.replace({ path: `/activities/${type}`, query: {} })
}
</script>

<template>
  <div class="yg-v2-page phx phx-activity">
    <header class="phx-head">
      <div class="phx-head__copy">
        <p class="phx-head__eyebrow">Activity · {{ tab }}</p>
        <h1 class="phx-head__title">义工活动</h1>
        <p class="phx-head__lead">
          查看你参与的、班级的与全校的义工活动；状态与时长以平台记录为准。
        </p>
      </div>
      <span class="phx-head__index" aria-hidden="true">02</span>
    </header>

    <nav class="phx-tabs" aria-label="义工活动范围">
      <span class="phx-tabs__label" aria-hidden="true">Scope</span>
      <button
        v-for="pane in panes.filter((item) => item.visibility)"
        :key="pane.value"
        type="button"
        class="phx-tab"
        :class="{ 'is-active': tab === pane.value }"
        :aria-current="tab === pane.value ? 'page' : undefined"
        @click="moveTo(pane.value)"
      >
        <span class="phx-tab__fill" aria-hidden="true"></span>
        <ElIcon class="phx-tab__icon" aria-hidden="true"><component :is="pane.icon" /></ElIcon>
        <span class="phx-tab__text">{{ t(`activity.view.panels.${pane.value}.short`) }}</span>
        <span class="phx-tab__code" aria-hidden="true">{{ pane.value }}</span>
      </button>
    </nav>

    <ZActivityList class="phx-list" :role="tab" :key="tab" />
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统 · 活动页（与 AboutView 同一套 scoped 写法）
   直角 + 不对称斜切、细线、offset 硬阴影；浅色只改强调色与硬阴影。
   注：本项目的 compiler-sfc 3.5 会把 `:global(x) .a` 整条替换成 `x`，
   所以浅色覆盖写成 `html[data-theme='light'] .phx`（编译后仍带 scoped 属性）。
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
  animation: phx-head-in 420ms var(--phx-ease) both;
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

/* ---------- 页签轨道：无填充块，只保留底部细线与倾斜强调线 ---------- */
.phx-tabs {
  position: relative;
  display: flex;
  align-items: stretch;
  gap: 4px;
  padding: 0;
  border-bottom: 1px solid var(--phx-line);
  animation: phx-slide-in 400ms 90ms var(--phx-ease) both;
}

.phx-tabs__label {
  display: flex;
  align-items: center;
  padding: 0 var(--yg-space-3) 0 0;
  margin-right: var(--yg-space-1);
  border-right: 1px solid var(--phx-line-soft);
  color: var(--phx-muted);
  font: 400 13px / 1 var(--phx-display);
  letter-spacing: 0.24em;
  text-transform: uppercase;
}

.phx-tab {
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-height: 44px;
  padding: 0 18px;
  border: 0;
  background: transparent;
  color: var(--phx-muted);
  cursor: pointer;
  font: inherit;
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  transition: color 200ms var(--phx-ease);
}

/* 激活标记：3px 倾斜底线，从中间展开；不再是整块斜切填充 */
.phx-tab__fill {
  position: absolute;
  right: 18px;
  bottom: -1px;
  left: 18px;
  z-index: 0;
  height: 3px;
  background: var(--phx-accent);
  transform: scaleX(0) skewX(-28deg);
  transform-origin: right center;
  transition: transform 340ms var(--phx-spring);
  pointer-events: none;
}

/* hover 只给一层很淡的斜向洗色，不形成方块 */
.phx-tab::before {
  content: '';
  position: absolute;
  inset: 4px 2px;
  z-index: -1;
  background: linear-gradient(
    100deg,
    color-mix(in srgb, var(--phx-accent) 10%, transparent) 0 46%,
    transparent 78%
  );
  opacity: 0;
  transition: opacity 180ms var(--phx-ease);
  pointer-events: none;
}

.phx-tab:hover {
  color: var(--phx-ink);
}

.phx-tab:hover::before {
  opacity: 1;
}

.phx-tab.is-active {
  color: var(--phx-accent);
  font-weight: var(--yg-font-weight-semibold);
}

.phx-tab.is-active .phx-tab__fill {
  transform: scaleX(1) skewX(-28deg);
  transform-origin: left center;
}

.phx-tab:focus-visible {
  outline: 2px solid var(--phx-accent);
  outline-offset: -4px;
}

.phx-tab__icon {
  font-size: 16px;
}

.phx-tab__code {
  color: currentColor;
  font: 400 12px / 1 var(--phx-display);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  opacity: 0.55;
}

/* ---------- 活动列表：硬边面板 + 角标 + offset 硬阴影 ---------- */
/* 列表根节点内含 ElDrawer：根上不能有 transform/filter/clip-path，否则会成为抽屉的包含块；
   所以进入动画与切角都落在 el-card 上。 */
.yg-v2-page > .phx-list {
  animation: none;
}

.phx-list > :deep(.el-card) {
  --el-card-bg-color: var(--phx-surface);
  --el-card-border-color: color-mix(in srgb, var(--phx-ink) 16%, transparent);
  --el-card-padding: var(--yg-space-4) var(--yg-space-4) var(--yg-space-2);
  position: relative;
  overflow: hidden;
  border-radius: 0;
  box-shadow: 5px 5px 0 var(--phx-hard);
  animation: phx-card-in 440ms 140ms var(--phx-ease) backwards;
}

.phx-list > :deep(.el-card:hover) {
  box-shadow: 5px 5px 0 var(--phx-hard);
}

/* 角标并入 ::before（左侧强调条 + 右上折角），把 ::after 让给全局流光边 */
.phx-list > :deep(.el-card)::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 3;
  background:
    linear-gradient(
        225deg,
        var(--phx-accent) 0 9px,
        transparent 9px 12px,
        var(--phx-line) 12px 13px,
        transparent 13px
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-accent), transparent 72%) left top / 2px 100% no-repeat;
  pointer-events: none;
}

.phx-list :deep(.el-card .el-button) {
  border-radius: 0;
}

/* 表格：HUD 表头 + 细线 + 不透明的固定列 */
.phx-list :deep(.el-table) {
  --el-table-header-bg-color: var(--phx-sunken);
  --el-fill-color-lighter: color-mix(in srgb, var(--phx-ink) 3%, var(--phx-surface));
  --phx-row-hover: color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface));
  margin-top: var(--yg-space-3);
  border-top: 1px solid var(--phx-line);
}

.phx-list :deep(.el-table__inner-wrapper::before) {
  background-color: var(--phx-line-soft);
}

.phx-list :deep(.el-table thead th.el-table__cell) {
  background-color: var(--phx-sunken);
  border-bottom: 1px solid var(--phx-line);
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.phx-list :deep(.el-table thead th.el-table__cell:first-child) {
  background-image: linear-gradient(var(--phx-accent), var(--phx-accent));
  background-position: left center;
  background-size: 2px 40%;
  background-repeat: no-repeat;
}

.phx-list :deep(.el-table .el-table__row > td.el-table__cell) {
  transition: background-color 180ms var(--phx-ease);
}

.phx-list :deep(.el-table .el-table__row > td.el-table__cell .cell) {
  transition: transform 260ms var(--phx-spring);
}

/* 行 hover：整行青色淡底；首格左侧青线 + 斜切亮片；内容右移 3px */
.phx-list :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell),
.phx-list :deep(.el-table .el-table__body tr.el-table__row.hover-row > td.el-table__cell) {
  background-color: var(--phx-row-hover);
  box-shadow: inset 0 -1px 0 var(--phx-line);
}

.phx-list :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell:first-child) {
  background-image: linear-gradient(
    105deg,
    color-mix(in srgb, var(--phx-accent) 26%, transparent) 0 46%,
    transparent 46%
  );
  box-shadow:
    inset 3px 0 0 var(--phx-accent),
    inset 0 -1px 0 var(--phx-line);
}

.phx-list :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell .cell) {
  transform: translateX(3px);
}

/* 固定列 sticky 单元格必须保持不透明，避免横向滚动时内容透出 */
.phx-list
  :deep(
    .el-table .el-table__body tr.el-table__row > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--phx-surface);
}

.phx-list
  :deep(
    .el-table
      .el-table__body
      tr.el-table__row--striped
      > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--el-fill-color-lighter);
}

.phx-list
  :deep(
    .el-table
      .el-table__body
      tr.el-table__row:hover
      > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--phx-row-hover);
}

/* 行进入：逐行斜向擦入（只动单元格，tr 上做位移会打断 sticky 固定列） */
.phx-list :deep(.el-table__body tr.el-table__row) {
  animation: none;
}

.phx-list :deep(.el-table__body tr.el-table__row > td.el-table__cell) {
  animation: phx-cell-in 360ms var(--phx-ease) backwards;
  animation-delay: var(--phx-row-delay, 0ms);
}

.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(2)) {
  --phx-row-delay: 30ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(3)) {
  --phx-row-delay: 60ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(4)) {
  --phx-row-delay: 90ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(5)) {
  --phx-row-delay: 120ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(6)) {
  --phx-row-delay: 150ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(7)) {
  --phx-row-delay: 175ms;
}
.phx-list :deep(.el-table__body tr.el-table__row:nth-of-type(n + 8)) {
  --phx-row-delay: 200ms;
}

/* 表头搜索框：直角、底部青线，聚焦时整框青线 */
.phx-list :deep(.el-table .el-input__wrapper) {
  border-radius: 0;
  background-color: var(--phx-surface);
  box-shadow:
    inset 0 0 0 1px var(--phx-line-soft),
    inset 0 -2px 0 var(--phx-line);
}

.phx-list :deep(.el-table .el-input__wrapper:hover) {
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--phx-ink) 22%, transparent),
    inset 0 -2px 0 var(--phx-accent);
}

.phx-list :deep(.el-table .el-input__wrapper.is-focus) {
  box-shadow:
    inset 0 0 0 1px var(--phx-accent),
    inset 0 -2px 0 var(--phx-accent);
}

/* 状态标签：直角 + 右下小切 */
.phx-list :deep(.el-tag) {
  border-radius: 0;
  font-weight: var(--yg-font-weight-medium);
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
}

html[data-theme='light'] .phx-list :deep(.el-tag) {
  color: var(--phx-accent);
  border-color: color-mix(in srgb, var(--phx-accent) 30%, transparent);
  background-color: color-mix(in srgb, var(--phx-accent) 8%, transparent);
}

html[data-theme='light'] .phx-list :deep(.el-tag.el-tag--success) {
  color: var(--yg-text-success);
  border-color: color-mix(in srgb, var(--yg-text-success) 30%, transparent);
  background-color: var(--yg-color-success-soft);
}

html[data-theme='light'] .phx-list :deep(.el-tag.el-tag--warning) {
  color: var(--yg-text-warning);
  border-color: color-mix(in srgb, var(--yg-text-warning) 30%, transparent);
  background-color: var(--yg-color-warning-soft);
}

html[data-theme='light'] .phx-list :deep(.el-tag.el-tag--danger) {
  color: var(--yg-text-danger);
  border-color: color-mix(in srgb, var(--yg-text-danger) 30%, transparent);
  background-color: var(--yg-color-danger-soft);
}

/* 分页（ZPagination 自绘）：几何与颜色交给共享组件，这里只补字体 */
.phx-list :deep(.yg-pg) {
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-weight: 500;
  letter-spacing: 0.03em;
}

.phx-list :deep(.yg-pg__num) {
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: 14px;
  font-weight: 700;
}

/* ---------- 动效 ---------- */
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

/* 卡片斜向 clip reveal；终点多留 12px 余量，结束时硬阴影不会“跳出来” */
@keyframes phx-card-in {
  from {
    opacity: 0;
    clip-path: polygon(-12px -12px, -12px -12px, -52px calc(100% + 12px), -52px calc(100% + 12px));
  }
  to {
    opacity: 1;
    clip-path: polygon(
      -12px -12px,
      calc(100% + 52px) -12px,
      calc(100% + 12px) calc(100% + 12px),
      -52px calc(100% + 12px)
    );
  }
}

@keyframes phx-cell-in {
  from {
    opacity: 0;
    clip-path: inset(0 100% 0 0);
  }
  to {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

/* ---------- 移动端（390）：页签只留图标 + 中文，均分宽度 ---------- */
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

  .phx-tabs {
    gap: 4px;
    padding-right: 0;
  }

  .phx-tabs__label,
  .phx-tab__code {
    display: none;
  }

  .phx-tab {
    flex: 1 1 0;
    justify-content: center;
    min-width: 0;
    padding: 0 12px;
  }

  .phx-list > :deep(.el-card) {
    --el-card-padding: var(--yg-space-3) var(--yg-space-2) var(--yg-space-1);
    box-shadow: 3px 3px 0 var(--phx-hard);
  }
}

@media (prefers-reduced-motion: reduce) {
  .phx-head,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .phx-tabs,
  .phx-list > :deep(.el-card),
  .phx-list :deep(.el-table__body tr.el-table__row > td.el-table__cell) {
    animation: none;
  }

  .phx-tab,
  .phx-tab__fill,
  .phx-tab::before,
  .phx-list :deep(.el-table .el-table__row > td.el-table__cell .cell) {
    transition: none;
  }

  .phx-tab:hover,
  .phx-list :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell .cell) {
    transform: none;
  }
}
</style>

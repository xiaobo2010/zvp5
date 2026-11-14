<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElIcon, ElSwitch, ElSkeleton } from 'element-plus'
import { Refresh, InfoFilled } from '@element-plus/icons-vue'
import { useUserTime, TIME_STANDARD, type TimeKey } from '@/composables/useUserTime'
import { useCountUp } from '@/composables/useCountUp'
import { useUserStore } from '@/stores/user'

/**
 * 首页「义工时间」：Apple Fitness 式三同心环。
 *
 * 红=校内 25h、绿=校外 15h、蓝=时间 18h（全站统一配色，见 tokens.css
 * 的 --yg2-cat-* ）。环用 SVG + stroke-dasharray 绘制，加载时从 0 长到实际
 * 进度；超过目标的环最多画满一圈，但下方百分比照实显示。
 */
const props = defineProps<{
  user: string
  onCampus?: number
  offCampus?: number
  socialPractice?: number
}>()

const { t } = useI18n()
const userStore = useUserStore()

const id = computed(() => props.user || userStore._id)
const { loading, error, exceed, shown, total, refresh } = useUserTime(() => id.value)

const meta: Record<TimeKey, { label: string; color: string; soft: string }> = {
  'on-campus': { label: '校内义工', color: 'var(--yg2-cat-school)', soft: 'var(--yg2-cat-school-soft)' },
  'off-campus': { label: '校外义工', color: 'var(--yg2-cat-community)', soft: 'var(--yg2-cat-community-soft)' },
  'social-practice': { label: '时间义工', color: 'var(--yg2-cat-time)', soft: 'var(--yg2-cat-time-soft)' }
}

const order: TimeKey[] = ['on-campus', 'off-campus', 'social-practice']

/** 三条环的半径与线宽（viewBox 200×200，圆心 100,100） */
const rings = [
  { key: 'on-campus' as TimeKey, r: 86, w: 13 },
  { key: 'off-campus' as TimeKey, r: 66, w: 13 },
  { key: 'social-practice' as TimeKey, r: 46, w: 13 }
]

function circumference(r: number) {
  return 2 * Math.PI * r
}

/** 0–1 的进度：超过目标也只画满一圈 */
function progress(key: TimeKey) {
  return Math.min(1, Math.max(0, shown.value[key] / TIME_STANDARD[key]))
}

function percent(key: TimeKey) {
  return Math.round((shown.value[key] / TIME_STANDARD[key]) * 100)
}

/**
 * 环只在数据就绪后才会挂载，若直接给终值，CSS transition 没有「起点」可比，
 * 动画不会发生。所以先画成空环（revealed=false），下一帧再放开。
 */
const revealed = ref(false)
watch(
  loading,
  (value) => {
    if (value) {
      revealed.value = false
      return
    }
    revealed.value = false
    window.setTimeout(() => {
      revealed.value = true
    }, 60)
  },
  { immediate: true }
)

function dashOffset(r: number, key: TimeKey) {
  const ratio = revealed.value ? progress(key) : 0
  return circumference(r) * (1 - ratio)
}

function fmt(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

const totalAnimated = useCountUp(() => total.value, 1100)
const animated = {
  'on-campus': useCountUp(() => shown.value['on-campus'], 1100),
  'off-campus': useCountUp(() => shown.value['off-campus'], 1100),
  'social-practice': useCountUp(() => shown.value['social-practice'], 1100)
}

/** 悬停强调：指向哪一条，其余两条降权 */
const hovered = ref<TimeKey | null>(null)
function dim(key: TimeKey) {
  return hovered.value !== null && hovered.value !== key
}

const formulaOpen = ref(false)
</script>

<template>
  <div class="tr">
    <header class="tr__head">
      <div>
        <h2 class="yg2-card__title">{{ t('home.panels.time.title') }}</h2>
        <p class="tr__hint">红=校内 · 绿=校外 · 蓝=时间</p>
      </div>
      <button class="yg2-icon-btn" type="button" title="刷新时长" aria-label="刷新时长" @click="refresh">
        <ElIcon><Refresh /></ElIcon>
      </button>
    </header>

    <p v-if="error" class="tr__error">{{ error }}</p>
    <ElSkeleton v-else-if="loading" :rows="4" animated />

    <template v-else>
      <div class="tr__body">
      <div class="tr__rings" :class="{ 'is-hovering': hovered !== null }">
        <svg viewBox="0 0 200 200" class="tr__svg" role="img" :aria-label="`累计 ${fmt(total)} 小时`">
          <g v-for="ring in rings" :key="ring.key" :class="{ 'is-dim': dim(ring.key) }">
            <!-- 轨道：同色低透明度 -->
            <circle
              cx="100"
              cy="100"
              :r="ring.r"
              fill="none"
              :stroke="meta[ring.key].soft"
              :stroke-width="ring.w"
              stroke-linecap="round"
            />
            <!-- 进度：从顶部顺时针 -->
            <circle
              class="tr__progress"
              cx="100"
              cy="100"
              :r="ring.r"
              fill="none"
              :stroke="meta[ring.key].color"
              :stroke-width="ring.w"
              stroke-linecap="round"
              :stroke-dasharray="circumference(ring.r)"
              :stroke-dashoffset="dashOffset(ring.r, ring.key)"
              :style="{
                transition: 'stroke-dashoffset 1100ms cubic-bezier(0.16, 0.84, 0.24, 1)',
                opacity: dim(ring.key) ? 0.28 : 1
              }"
              @pointerenter="hovered = ring.key"
              @pointerleave="hovered = null"
            />
          </g>
        </svg>

        <div class="tr__center">
          <strong class="tr__total">{{ fmt(totalAnimated) }}</strong>
          <span class="tr__total-label">累计义工小时</span>
        </div>
      </div>

      <ul class="tr__legend">
        <li
          v-for="key in order"
          :key="key"
          class="tr__item"
          :class="{ 'is-dim': dim(key), 'is-done': percent(key) >= 100 }"
          @pointerenter="hovered = key"
          @pointerleave="hovered = null"
        >
          <span class="tr__dot" :style="{ background: meta[key].color }" />
          <span class="tr__name">{{ meta[key].label }}</span>
          <span class="tr__value">
            <strong>{{ fmt(animated[key].value) }}</strong>h
            <span class="tr__goal">/ {{ TIME_STANDARD[key] }}h</span>
          </span>
          <span class="tr__pct">
            {{ percent(key) }}%
            <span v-if="percent(key) >= 100" class="tr__badge">已达标</span>
          </span>
        </li>
      </ul>
      </div>
    </template>

    <footer class="tr__foot">
      <label class="tr__switch">
        <span>{{ t('home.panels.time.discount') }}</span>
        <ElSwitch v-model="exceed" size="small" aria-label="校内校外时长互补" />
      </label>
      <button class="tr__formula" type="button" @click="formulaOpen = true">
        <ElIcon><InfoFilled /></ElIcon>
        折算公示
      </button>
    </footer>

    <Teleport to="body">
      <div
        v-if="formulaOpen"
        class="tr-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tr-formula-title"
        @click.self="formulaOpen = false"
      >
        <div class="tr-modal__card">
          <h3 id="tr-formula-title" class="tr-modal__title">折算公示</h3>
          <p class="tr__formula-desc">
            达标线：校内 25 小时、校外 15 小时、时间义工 18 小时。打开「显示折算」后，超出的部分按下表互补：
          </p>
          <table class="tr__table">
            <thead>
              <tr><th>来源</th><th>超出部分</th><th>折算为</th></tr>
            </thead>
            <tbody>
              <tr><td>校内</td><td>超过 25 小时</td><td>每 3 小时折 1 小时校外</td></tr>
              <tr><td>校外</td><td>超过 15 小时</td><td>每 2 小时折 1 小时校内</td></tr>
            </tbody>
          </table>
          <p class="tr__formula-desc">
            折算结果四舍五入到整小时，只影响展示，不改变数据库里的原始时长。
          </p>
          <div class="tr-modal__foot">
            <button class="yg2-btn yg2-btn--primary yg2-btn--sm" type="button" @click="formulaOpen = false">
              知道了
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.tr {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
  container-type: inline-size;
}

.tr__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.tr__hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.tr__error {
  margin: 0;
  font-size: 13px;
  color: var(--yg2-danger);
}

/* ---------- 圆环 ---------- */
.tr__rings {
  position: relative;
  display: grid;
  place-items: center;
  width: min(252px, 70cqw);
  margin: 2px auto 0;
  aspect-ratio: 1;
}

.tr__svg {
  width: 100%;
  height: 100%;
  overflow: visible;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
}

.tr__progress {
  transition: opacity 220ms var(--yg2-ease-out);
}

.tr__center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  pointer-events: none;
}

.tr__total {
  font-size: clamp(30px, 13cqw, 44px);
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.tr__total-label {
  font-size: 12px;
  color: var(--yg2-text-secondary);
}

/* ---------- 图例 ---------- */
.tr__legend {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}

.tr__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 8px 10px;
  border-radius: var(--yg2-r-12);
  transition:
    background 200ms var(--yg2-ease-out),
    opacity 200ms var(--yg2-ease-out);
}

.tr__item.is-dim {
  opacity: 0.45;
}

.tr__item:hover {
  background: color-mix(in srgb, var(--yg2-text) 4%, transparent);
}

.tr__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-bottom: 2px;
}

.tr__name {
  font-size: 12px;
  color: var(--yg2-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tr__value {
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.tr__value strong {
  font-size: 17px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--yg2-text);
  font-variant-numeric: tabular-nums;
}

.tr__goal {
  font-size: 11.5px;
}

.tr__pct {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--yg2-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.tr__badge {
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10.5px;
  color: var(--yg2-success);
  background: var(--yg2-success-soft);
}

/* ---------- 底部 ---------- */
.tr__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--yg2-sep);
}

.tr__switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--yg2-text-secondary);
  cursor: pointer;
}

.tr__formula {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--yg2-accent);
  background: var(--yg2-accent-soft);
  transition: background 180ms var(--yg2-ease-out);
}

.tr__formula:hover {
  background: color-mix(in srgb, var(--yg2-accent) 18%, transparent);
}

/* ---------- 公示弹层 ---------- */
.tr__formula-desc {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--yg2-text-secondary);
}

.tr__table {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 12.5px;
}

.tr__table th,
.tr__table td {
  padding: 8px 6px;
  text-align: left;
  border-bottom: 1px solid var(--yg2-sep);
}

.tr__table th {
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

.tr-modal {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: tr-fade 180ms var(--yg2-ease-out);
}

.tr-modal__card {
  width: min(440px, 100%);
  padding: 22px 22px 16px;
  border-radius: var(--yg2-r-26);
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  box-shadow: 0 30px 80px -24px rgba(0, 0, 0, 0.5);
  animation: tr-pop 260ms cubic-bezier(0.34, 1.32, 0.64, 1);
}

.tr-modal__title {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 600;
}

.tr-modal__foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

@keyframes tr-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes tr-pop {
  from { opacity: 0; transform: translateY(8px) scale(0.98); }
  to { opacity: 1; transform: none; }
}

@container (max-width: 420px) {
  .tr__legend {
    grid-template-columns: 1fr;
  }

  .tr__item {
    flex-direction: row;
    align-items: center;
    gap: 8px;
  }

  .tr__dot {
    margin-bottom: 0;
  }

  .tr__name {
    flex: 1;
  }
}

/* ---------- 宽卡片：左环右表（Apple Fitness 的排法） ---------- */
.tr__body {
  display: grid;
  gap: 8px 22px;
  align-items: center;
}

@container (min-width: 520px) {
  .tr__body {
    grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  }

  .tr__legend {
    grid-template-columns: 1fr;
    gap: 6px;
    align-content: center;
  }

  .tr__item {
    flex-direction: row;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
  }

  .tr__item .tr__dot {
    margin-bottom: 0;
  }

  .tr__item .tr__name {
    flex: 1 1 auto;
  }

  .tr__item .tr__pct {
    margin-left: auto;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tr__progress {
    transition: none !important;
  }
}
</style>

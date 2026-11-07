<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, toRefs, watch } from 'vue'
import { ElButton, ElDialog, ElIcon } from 'element-plus'
import { DataAnalysis, Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { ZDetailedAnalysis } from '@/components'
import api from '@/api'
import type { UserActivityTimeSums } from '@/../types'

/*
 * Phi HUD 时长面板：替换首页的 ZUserTimeJudge 圆环。
 * 业务口径与 ZUserTimeJudge 保持一致：
 *  - 数据源 api.user.time.read（本人 / secretary 及以上可读，后端为准）
 *  - 互补折算：校外 += round((校内 - 25) / 3)，校内 += round((校外 - 15) / 2)
 *  - 标准：校内 25 / 校外 15 / 社会实践 18
 *  - 总记录 = 三项原始值相加（不含折算）
 */

const props = withDefaults(
  defineProps<{
    user: string
    onCampus?: number
    offCampus?: number
    socialPractice?: number
  }>(),
  {
    onCampus: 0,
    offCampus: 0,
    socialPractice: 0
  }
)

const { user, onCampus, offCampus, socialPractice } = toRefs(props)
const userStore = useUserStore()
const { t } = useI18n()

const SEGMENTS = 24
const TWEEN_MS = 640
const STANDARD = { on: 25, off: 15, social: 18 } as const

const base = reactive({
  onCampus: onCampus.value,
  offCampus: offCampus.value,
  socialPractice: socialPractice.value
})

const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const reportPage = ref(false)
const sweepKey = ref(0)

const canRead = computed(
  () =>
    user.value === userStore._id ||
    userStore.position.includes('admin') ||
    userStore.position.includes('department') ||
    userStore.position.includes('secretary')
)

function getDiscount(value: number, threshold: number, rate: number) {
  if (value <= threshold) return 0
  return Math.round((value - threshold) / rate)
}

const bonusOn = computed(() => getDiscount(base.offCampus, 15, 2))
const bonusOff = computed(() => getDiscount(base.onCampus, 25, 3))
const hasBonus = computed(() => bonusOn.value > 0 || bonusOff.value > 0)

const target = computed(() => ({
  on: base.onCampus + bonusOn.value,
  off: base.offCampus + bonusOff.value,
  social: base.socialPractice,
  total: roundTo(base.onCampus + base.offCampus + base.socialPractice, 2)
}))

const sums = computed<UserActivityTimeSums>(() => ({
  'on-campus': base.onCampus,
  'off-campus': base.offCampus,
  'social-practice': base.socialPractice
}))

function roundTo(value: number, digits: number) {
  const factor = 10 ** digits
  return Math.round(value * factor) / factor
}

function decimalsOf(value: number) {
  const text = String(value)
  const dot = text.indexOf('.')
  return dot === -1 ? 0 : Math.min(text.length - dot - 1, 2)
}

/* ---------- 数字缓动：easeOutExpo，0 → 真实值，≤650ms ---------- */
type Key = 'on' | 'off' | 'social' | 'total'
const KEYS: Key[] = ['on', 'off', 'social', 'total']
const shown = reactive<Record<Key, number>>({ on: 0, off: 0, social: 0, total: 0 })
let frame = 0
let from: Record<Key, number> = { on: 0, off: 0, social: 0, total: 0 }
let startedAt = 0

function reducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

function easeOutExpo(x: number) {
  return x >= 1 ? 1 : 1 - 2 ** (-10 * x)
}

function settle() {
  for (const key of KEYS) shown[key] = target.value[key]
}

function step(now: number) {
  const progress = Math.min((now - startedAt) / TWEEN_MS, 1)
  const eased = easeOutExpo(progress)
  for (const key of KEYS) {
    shown[key] = from[key] + (target.value[key] - from[key]) * eased
  }
  if (progress < 1) {
    frame = requestAnimationFrame(step)
    return
  }
  frame = 0
  settle()
}

function animate() {
  if (frame) cancelAnimationFrame(frame)
  frame = 0
  if (reducedMotion() || typeof requestAnimationFrame !== 'function') {
    settle()
    return
  }
  from = { ...shown }
  startedAt = performance.now()
  sweepKey.value += 1
  frame = requestAnimationFrame(step)
}

watch(target, animate, { deep: true })

// 外部（store）刷新时长时同步原始值；本组件请求成功也会写回 store，值相同不会抖动
watch([onCampus, offCampus, socialPractice], ([on, off, social]) => {
  base.onCampus = on
  base.offCampus = off
  base.socialPractice = social
})

onMounted(animate)
onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
})

async function getTime() {
  if (!user.value) return
  if (!canRead.value) {
    error.value = '无权限查看该用户时长'
    loading.value = false
    loaded.value = true
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await api.user.time.read(user.value)
    if (result) {
      base.onCampus = result['on-campus']
      base.offCampus = result['off-campus']
      base.socialPractice = result['social-practice']
      if (user.value === userStore._id) {
        userStore.setTime(result)
      }
    }
    // 值未变化时 target 不会触发，手动刷新也要给一次扫光反馈
    animate()
  } catch {
    error.value = '时长数据加载失败，请稍后重试'
  } finally {
    loading.value = false
    loaded.value = true
  }
}

void getTime()

/* ---------- 三个 HUD 单元 ---------- */
const cells = computed(() => {
  const items = [
    {
      key: 'on' as const,
      index: '01',
      code: 'ON-CAMPUS',
      name: t('activity.mode.on-campus.name'),
      standard: STANDARD.on,
      real: target.value.on,
      bonus: bonusOn.value
    },
    {
      key: 'off' as const,
      index: '02',
      code: 'OFF-CAMPUS',
      name: t('activity.mode.off-campus.name'),
      standard: STANDARD.off,
      real: target.value.off,
      bonus: bonusOff.value
    },
    {
      key: 'social' as const,
      index: '03',
      code: 'SOCIAL',
      name: t('activity.mode.social-practice.name'),
      standard: STANDARD.social,
      real: target.value.social,
      bonus: 0
    }
  ]
  return items.map((item) => {
    const diff = roundTo(item.real - item.standard, 2)
    const ratio = item.standard ? item.real / item.standard : 0
    const live = item.standard ? shown[item.key] / item.standard : 0
    return {
      ...item,
      display: shown[item.key].toFixed(decimalsOf(item.real)),
      percent: Math.round(ratio * 100),
      lit: Math.max(0, Math.min(SEGMENTS, Math.floor(live * SEGMENTS + 1e-6))),
      over: diff >= 0,
      gapLabel: diff >= 0 ? t('home.panels.time.more') : t('home.panels.time.less'),
      gap: Math.abs(diff).toFixed(diff % 1 === 0 ? 0 : 1)
    }
  })
})

const totalText = computed(() => shown.total.toFixed(decimalsOf(target.value.total)))
// store 里已有缓存时长时直接展示并在原位缓动；只有完全没有数据的首次加载才出骨架
const showSkeleton = computed(() => loading.value && !loaded.value && target.value.total === 0)
</script>

<template>
  <div class="phi-time">
    <ElDialog v-model="reportPage" fullscreen destroy-on-close title="义工时长明细">
      <ZDetailedAnalysis :id="user" mode="on-campus" :sums="sums" />
    </ElDialog>

    <section
      class="phi-time__panel"
      :class="{ 'is-loading': loading }"
      aria-labelledby="phi-time-title"
    >
      <span :key="sweepKey" class="phi-time__sweep" aria-hidden="true"></span>

      <header class="phi-time__head">
        <div class="phi-time__heading">
          <span class="phi-time__tag" aria-hidden="true">TIME // HUD</span>
          <h2 id="phi-time-title" class="phi-time__title">{{ t('home.panels.time.title') }}</h2>
        </div>

        <div class="phi-time__total" aria-live="polite">
          <span class="phi-time__total-label">总记录</span>
          <span class="phi-time__total-value"> {{ totalText }}<small>H</small> </span>
        </div>

        <div class="phi-time__tools">
          <ElButton
            class="phi-time__tool"
            aria-label="查看时长明细"
            title="查看时长明细"
            @click="reportPage = true"
          >
            <ElIcon><DataAnalysis /></ElIcon>
            <span>明细</span>
          </ElButton>
          <ElButton
            class="phi-time__tool"
            :loading="loading"
            :disabled="loading"
            aria-label="刷新时长"
            title="刷新时长"
            @click="getTime"
          >
            <ElIcon v-if="!loading"><Refresh /></ElIcon>
            <span>刷新</span>
          </ElButton>
        </div>
      </header>

      <div v-if="error && !loading" class="phi-time__state" role="alert">
        <span class="phi-time__state-code" aria-hidden="true">ERR</span>
        <p>{{ error }}</p>
        <ElButton v-if="canRead" class="phi-time__tool" @click="getTime">重新加载</ElButton>
      </div>

      <div v-else-if="showSkeleton" class="phi-time__grid" aria-busy="true">
        <div v-for="n in 3" :key="n" class="phi-time__cell is-skeleton">
          <span class="phi-time__skel phi-time__skel--sm"></span>
          <span class="phi-time__skel phi-time__skel--lg"></span>
          <span class="phi-time__skel phi-time__skel--bar"></span>
        </div>
      </div>

      <div v-else class="phi-time__grid">
        <article
          v-for="(cell, i) in cells"
          :key="cell.key"
          class="phi-time__cell"
          :class="{ 'is-over': cell.over }"
          :style="{ '--i': i }"
        >
          <div class="phi-time__cell-top">
            <span class="phi-time__index" aria-hidden="true">{{ cell.index }}</span>
            <span class="phi-time__name">
              <b>{{ cell.name }}</b>
              <small aria-hidden="true">{{ cell.code }}</small>
            </span>
            <span v-if="cell.bonus > 0" class="phi-time__bonus">折算 +{{ cell.bonus }}</span>
          </div>

          <p class="phi-time__value">
            <span class="phi-time__num" aria-hidden="true">{{ cell.display }}</span>
            <span class="phi-time__unit" aria-hidden="true">H</span>
            <span
              class="phi-time__gap"
              :class="cell.over ? 'is-over' : 'is-under'"
              aria-hidden="true"
            >
              <span class="phi-time__gap-label">{{ cell.gapLabel }}</span>
              <span class="phi-time__gap-num">{{ cell.gap }}</span>
              <small>H</small>
            </span>
            <span class="phi-time__sr">
              {{ cell.name }} {{ cell.real }} {{ t('home.panels.time.unit') }}，标准
              {{ cell.standard }} {{ t('home.panels.time.unit') }}，{{ cell.gapLabel }}
              {{ cell.gap }} {{ t('home.panels.time.unit') }}
            </span>
          </p>

          <div
            class="phi-time__ticks"
            role="progressbar"
            :aria-label="`${cell.name}完成度`"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-valuenow="Math.min(cell.percent, 100)"
            :style="{ '--lit-pct': `${(cell.lit / SEGMENTS) * 100}%` }"
          >
            <i
              v-for="n in SEGMENTS"
              :key="n"
              :class="{ 'is-lit': n <= cell.lit, 'is-major': n % 6 === 0 }"
              :style="{ '--tick-i': n }"
            ></i>
          </div>

          <dl class="phi-time__meta">
            <div>
              <dt>标准</dt>
              <dd>{{ cell.standard }}<small>H</small></dd>
            </div>
            <div>
              <dt>进度</dt>
              <dd>{{ cell.percent }}<small>%</small></dd>
            </div>
          </dl>
        </article>
      </div>

      <p v-if="hasBonus && !error" class="phi-time__note">
        已按互补规则折算：校内超出 25 小时部分按 1/3 计入校外，校外超出 15 小时部分按 1/2
        计入校内。总记录为三项原始时长之和。
      </p>
    </section>
  </div>
</template>

<style scoped>
.phi-time {
  --tp-cut: 18px;
  --tp-cell-cut: 12px;
  min-width: 0;
}

/* ---------- 外框：硬边 + 不对称切角 + 硬阴影（polygon 包住阴影） ---------- */
.phi-time__panel {
  --tp-d: var(--phi-shadow-d, 6px);
  position: relative;
  overflow: hidden;
  padding: var(--yg-space-5) var(--yg-space-6) var(--yg-space-6);
  border: 1px solid var(--phi-frame);
  border-radius: 0;
  background:
    linear-gradient(var(--phi-scan) 1px, transparent 1px) 0 0 / 100% 3px,
    linear-gradient(115deg, var(--phi-panel-hi), var(--phi-panel) 58%);
  box-shadow: var(--tp-d) var(--tp-d) 0 var(--phi-shadow);
  color: var(--phi-ink);
  clip-path: polygon(
    0 0,
    calc(100% - var(--tp-cut)) 0,
    calc(100% + var(--tp-d)) calc(var(--tp-cut) + var(--tp-d)),
    calc(100% + var(--tp-d)) calc(100% + var(--tp-d)),
    calc(var(--tp-cut) + var(--tp-d)) calc(100% + var(--tp-d)),
    0 calc(100% - var(--tp-cut))
  );
  animation: tp-reveal 560ms cubic-bezier(0.16, 1, 0.3, 1) 120ms backwards;
}

/* 切角斜线 + 左侧青色竖条 + 角括号 */
.phi-time__panel::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phi-frame) calc(50% - 1px),
        var(--phi-frame) calc(50% + 1px),
        transparent calc(50% + 1px)
      )
      100% 0 / var(--tp-cut) var(--tp-cut) no-repeat,
    linear-gradient(
        to top right,
        transparent calc(50% - 1px),
        var(--phi-frame) calc(50% - 1px),
        var(--phi-frame) calc(50% + 1px),
        transparent calc(50% + 1px)
      )
      0 100% / var(--tp-cut) var(--tp-cut) no-repeat,
    linear-gradient(var(--phi-accent), var(--phi-accent)) 0 0 / 3px 46% no-repeat,
    linear-gradient(var(--phi-accent), var(--phi-accent)) calc(100% - 10px) calc(100% - 10px) / 18px
      2px no-repeat,
    linear-gradient(var(--phi-accent), var(--phi-accent)) calc(100% - 10px) calc(100% - 10px) / 2px
      18px no-repeat;
}

@keyframes tp-reveal {
  from {
    clip-path: polygon(0 0, 0 0, 0 0, 0 100%, 0 100%, 0 100%);
  }
}

/* 刷新扫光：青色光带一次性横扫 */
.phi-time__sweep {
  position: absolute;
  inset: 0 auto 0 0;
  z-index: 2;
  width: 38%;
  pointer-events: none;
  background: linear-gradient(
    100deg,
    transparent 0%,
    var(--phi-accent-soft) 40%,
    var(--phi-accent-glow) 52%,
    transparent 64%
  );
  transform: translateX(-110%) skewX(-18deg);
  animation: tp-sweep 620ms cubic-bezier(0.7, 0, 0.2, 1) both;
}

@keyframes tp-sweep {
  to {
    transform: translateX(320%) skewX(-18deg);
  }
}

/* ---------- 头部 ---------- */
.phi-time__head {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: end;
  gap: var(--yg-space-5);
  padding-bottom: var(--yg-space-4);
  border-bottom: 1px solid var(--phi-line);
}

.phi-time__tag {
  display: inline-block;
  margin-bottom: var(--yg-space-2);
  padding: 2px 8px 1px;
  background: var(--phi-ink);
  color: var(--phi-panel);
  font: 700 11px / 1.4 var(--phi-hud);
  letter-spacing: 0.22em;
  clip-path: polygon(0 0, 100% 0, calc(100% - 6px) 100%, 0 100%);
}

.phi-time__title {
  margin: 0;
  color: var(--phi-ink);
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: 1.1;
}

.phi-time__total {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  padding: 0 var(--yg-space-4);
  border-right: 1px solid var(--phi-line);
}

.phi-time__total-label {
  color: var(--phi-ink-3);
  font-size: var(--yg-font-size-xs);
  letter-spacing: 0.12em;
}

.phi-time__total-value {
  color: var(--phi-ink);
  font: 700 34px / 0.9 var(--phi-hud);
  font-variant-numeric: tabular-nums;
}

.phi-time__total-value small,
.phi-time__unit,
.phi-time__meta small {
  margin-left: 2px;
  color: var(--phi-accent-ink);
  font-family: var(--phi-hud);
  font-weight: 700;
}

.phi-time__total-value small {
  font-size: 16px;
}

.phi-time__tools {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

/* ---------- 网格与单元 ---------- */
.phi-time__grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--yg-space-4);
  margin-top: var(--yg-space-5);
}

.phi-time__cell {
  position: relative;
  min-width: 0;
  padding: var(--yg-space-4) var(--yg-space-4) var(--yg-space-4) var(--yg-space-5);
  border: 1px solid var(--phi-line-strong);
  background:
    linear-gradient(90deg, var(--phi-grid) 1px, transparent 1px) 0 0 / 22px 22px,
    linear-gradient(var(--phi-grid) 1px, transparent 1px) 0 0 / 22px 22px,
    var(--phi-sunken);
  clip-path: polygon(
    0 0,
    calc(100% - var(--tp-cell-cut)) 0,
    100% var(--tp-cell-cut),
    100% 100%,
    var(--tp-cell-cut) 100%,
    0 calc(100% - var(--tp-cell-cut))
  );
  animation: tp-cell 460ms cubic-bezier(0.16, 1, 0.3, 1) backwards;
  animation-delay: calc(220ms + var(--i, 0) * 70ms);
}

.phi-time__cell::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--phi-line-strong);
  transition: background-color 240ms ease;
}

.phi-time__cell.is-over::before {
  background: var(--phi-accent);
}

.phi-time__cell::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: var(--tp-cell-cut);
  height: var(--tp-cell-cut);
  background: linear-gradient(
    to bottom left,
    transparent calc(50% - 1px),
    var(--phi-line-strong) calc(50% - 1px),
    var(--phi-line-strong) calc(50% + 1px),
    transparent calc(50% + 1px)
  );
}

@keyframes tp-cell {
  from {
    opacity: 0;
    transform: translateX(-14px);
    clip-path: polygon(0 0, 0 0, 0 0, 0 100%, 0 100%, 0 100%);
  }
}

.phi-time__cell-top {
  display: flex;
  align-items: flex-start;
  gap: var(--yg-space-3);
}

.phi-time__index {
  color: var(--phi-accent-ink);
  font: 400 30px / 0.8 var(--phi-display);
  letter-spacing: 0.02em;
}

.phi-time__name {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.phi-time__name b {
  color: var(--phi-ink);
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-semibold);
}

.phi-time__name small {
  color: var(--phi-ink-3);
  font: 500 10px / 1 var(--phi-hud);
  letter-spacing: 0.2em;
}

.phi-time__bonus {
  flex: 0 0 auto;
  padding: 1px 6px;
  border: 1px solid var(--phi-accent-ink);
  color: var(--phi-accent-ink);
  font: 700 11px / 1.4 var(--phi-hud);
  letter-spacing: 0.04em;
}

.phi-time__value {
  display: flex;
  align-items: baseline;
  margin: var(--yg-space-4) 0 var(--yg-space-3);
  color: var(--phi-ink);
}

.phi-time__num {
  font: 700 clamp(48px, 5.2vw, 64px) / 0.82 var(--phi-hud);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

.phi-time__cell.is-over .phi-time__num {
  text-shadow: 2px 0 var(--phi-accent);
}

.phi-time__unit {
  font-size: 22px;
}

/* 折算差额：紧贴大数字右上角，用另一种形态（描边小牌）和颜色承载“少/多” */
.phi-time__gap {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  margin-left: var(--yg-space-3);
  padding: 3px 8px 2px;
  border: 1px solid currentColor;
  font-family: var(--phi-hud);
  font-size: var(--yg-font-size-xs);
  letter-spacing: 0.08em;
  line-height: 1;
  transform: translateY(-5px);
  clip-path: polygon(0 0, calc(100% - 6px) 0, 100% 6px, 100% 100%, 0 100%);
}

.phi-time__gap.is-over {
  color: var(--phi-accent-ink);
  background: color-mix(in srgb, var(--phi-accent) 12%, transparent);
}

.phi-time__gap.is-under {
  color: var(--phi-warn);
  background: color-mix(in srgb, var(--phi-warn) 12%, transparent);
}

.phi-time__gap-label {
  opacity: 0.78;
}

.phi-time__gap small {
  font-size: 11px;
  font-weight: 700;
}

/* 故障抖动：沿用 ZVMS 标题的粉/青双影 + 偶发位移/倾斜 */
.phi-time__gap-num {
  position: relative;
  display: inline-block;
  min-width: 1ch;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: right;
  text-shadow:
    1px 0 color-mix(in srgb, var(--phi-pink) 72%, transparent),
    -1px 0 color-mix(in srgb, var(--phi-cyan) 72%, transparent);
  animation: tp-gap-glitch 6.4s steps(2) infinite;
}

@keyframes tp-gap-glitch {
  0%,
  90%,
  100% {
    transform: none;
    text-shadow:
      1px 0 color-mix(in srgb, var(--phi-pink) 72%, transparent),
      -1px 0 color-mix(in srgb, var(--phi-cyan) 72%, transparent);
  }
  91% {
    transform: translate(-1px, 1px) skewX(-6deg);
    text-shadow:
      -2px 0 var(--phi-pink),
      2px 0 var(--phi-cyan);
  }
  92% {
    transform: translate(2px, -1px) skewX(4deg);
    text-shadow:
      2px 0 var(--phi-pink),
      -2px 0 var(--phi-cyan);
    opacity: 0.86;
  }
  93% {
    transform: none;
    opacity: 1;
  }
}

.phi-time__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* 刻度条：24 段；点亮数跟随缓动中的数字，天然形成先快后慢的 stagger */
.phi-time__ticks {
  position: relative;
  display: grid;
  grid-template-columns: repeat(24, minmax(0, 1fr));
  gap: 3px;
  height: 18px;
  align-items: end;
  isolation: isolate;
}

/* 已点亮区域的低频扫光：光带用 background-position 在点亮段内推进，
   起始位置锁在进度条左端（x=0），不再从左端外侧飘进来。 */
.phi-time__ticks::before {
  content: '';
  position: absolute;
  z-index: 2;
  inset: 0 auto 0 0;
  width: var(--lit-pct, 0%);
  pointer-events: none;
  background-image: linear-gradient(
    90deg,
    transparent 0%,
    var(--phi-accent-soft) 42%,
    var(--phi-accent-glow) 50%,
    transparent 58%
  );
  background-repeat: no-repeat;
  background-size: 36% 100%;
  background-position: 0 0;
  animation: tp-bar-scan 3400ms cubic-bezier(0.55, 0, 0.2, 1) infinite;
}

/* 进度边缘的定位光标；随缓动数字一起推进 */
.phi-time__ticks::after {
  content: '';
  position: absolute;
  z-index: 3;
  top: 0;
  bottom: 0;
  left: clamp(0px, var(--lit-pct, 0%), calc(100% - 2px));
  width: 2px;
  pointer-events: none;
  background: var(--phi-accent);
  box-shadow:
    0 0 8px var(--phi-accent-glow),
    0 0 18px var(--phi-accent-soft);
  transition: left 340ms cubic-bezier(0.16, 1, 0.3, 1);
  animation: tp-edge-pulse 2200ms ease-in-out infinite;
}

.phi-time__ticks i {
  position: relative;
  z-index: 1;
  display: block;
  height: 10px;
  background: var(--phi-tick-off);
  transform-origin: 50% 100%;
  transition:
    height 260ms cubic-bezier(0.34, 1.56, 0.64, 1),
    background-color 160ms ease;
}

.phi-time__ticks i.is-major {
  height: 14px;
}

.phi-time__ticks i.is-lit {
  height: 18px;
  background: var(--phi-accent);
  animation: tp-tick 360ms cubic-bezier(0.34, 1.56, 0.64, 1);
  animation-delay: calc(var(--tick-i, 1) * 16ms);
}

.phi-time__cell:not(.is-over) .phi-time__ticks i.is-lit {
  background: var(--phi-ink);
}

@keyframes tp-tick {
  0% {
    transform: scaleY(0.2);
    filter: brightness(1.8);
  }
  60% {
    transform: scaleY(1.15);
  }
  100% {
    transform: none;
  }
}

@keyframes tp-bar-scan {
  0% {
    background-position: 0 0;
    opacity: 0;
  }
  8% {
    opacity: 0.55;
  }
  80% {
    background-position: 156% 0;
    opacity: 0.3;
  }
  88%,
  100% {
    background-position: 156% 0;
    opacity: 0;
  }
}

@keyframes tp-edge-pulse {
  0%,
  100% {
    opacity: 0.5;
    transform: scaleY(0.72);
  }
  50% {
    opacity: 1;
    transform: scaleY(1);
  }
}

.phi-time__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: var(--yg-space-4) 0 0;
  border-top: 1px dashed var(--phi-line-strong);
}

.phi-time__meta > div {
  min-width: 0;
  padding: var(--yg-space-2) 0 0;
}

.phi-time__meta > div + div {
  padding-left: var(--yg-space-3);
  border-left: 1px solid var(--phi-line);
}

.phi-time__meta dt {
  color: var(--phi-ink-3);
  font-size: var(--yg-font-size-xs);
}

.phi-time__meta dd {
  margin: 2px 0 0;
  color: var(--phi-ink);
  font: 700 20px / 1 var(--phi-hud);
  font-variant-numeric: tabular-nums;
}

.phi-time__meta small {
  font-size: 12px;
}

.phi-time__note {
  position: relative;
  z-index: 1;
  margin: var(--yg-space-4) 0 0;
  padding-left: var(--yg-space-3);
  border-left: 2px solid var(--phi-accent);
  color: var(--phi-ink-3);
  font-size: var(--yg-font-size-xs);
  line-height: 1.6;
}

/* ---------- 状态：错误 / 骨架 ---------- */
.phi-time__state {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: var(--yg-space-4);
  margin-top: var(--yg-space-5);
  padding: var(--yg-space-4) var(--yg-space-5);
  border: 1px solid var(--phi-warn);
  background: var(--phi-sunken);
  color: var(--phi-ink);
  clip-path: polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%);
}

.phi-time__state p {
  flex: 1;
  margin: 0;
}

.phi-time__state-code {
  color: var(--phi-warn);
  font: 400 28px / 1 var(--phi-display);
  letter-spacing: 0.06em;
}

.phi-time__cell.is-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  animation: none;
}

.phi-time__skel {
  display: block;
  background:
    linear-gradient(90deg, transparent 0%, var(--phi-accent-soft) 50%, transparent 100%) 0 0 / 200%
      100% no-repeat,
    var(--phi-tick-off);
  animation: tp-skel 1.1s linear infinite;
}

.phi-time__skel--sm {
  width: 40%;
  height: 14px;
}

.phi-time__skel--lg {
  width: 55%;
  height: 52px;
}

.phi-time__skel--bar {
  width: 100%;
  height: 14px;
}

@keyframes tp-skel {
  from {
    background-position:
      -100% 0,
      0 0;
  }
  to {
    background-position:
      200% 0,
      0 0;
  }
}

/* ---------- 工具按钮：方角 + 位移 ---------- */
.phi-time :deep(.phi-time__tool.el-button) {
  height: 34px;
  margin: 0;
  padding: 0 12px;
  gap: 6px;
}

.phi-time :deep(.phi-time__tool.el-button > span) {
  gap: 6px;
}

/* ---------- 响应式 ---------- */
@media (max-width: 959px) {
  .phi-time__head {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .phi-time__tools {
    grid-column: 1 / -1;
    flex-wrap: wrap;
  }

  .phi-time__grid {
    grid-template-columns: 1fr;
  }

  .phi-time__num {
    font-size: 56px;
  }
}

@media (max-width: 599px) {
  .phi-time {
    --tp-cut: 14px;
  }

  .phi-time__panel {
    padding: var(--yg-space-4);
  }

  .phi-time__head {
    gap: var(--yg-space-3);
  }

  .phi-time__total {
    padding: 0;
    border-right: 0;
  }

  .phi-time__total-value {
    font-size: 28px;
  }

  .phi-time__ticks {
    gap: 2px;
  }

  .phi-time__meta > div + div {
    padding-left: var(--yg-space-2);
  }

  .phi-time__state {
    flex-wrap: wrap;
  }
}

@media (prefers-reduced-motion: reduce) {
  .phi-time__panel,
  .phi-time__cell,
  .phi-time__ticks i.is-lit,
  .phi-time__skel,
  .phi-time__gap-num {
    animation: none;
  }

  .phi-time__sweep {
    display: none;
  }

  .phi-time__ticks::before,
  .phi-time__ticks::after {
    animation: none;
    transition: none;
  }

  .phi-time__ticks i {
    transition: none;
  }
}
</style>

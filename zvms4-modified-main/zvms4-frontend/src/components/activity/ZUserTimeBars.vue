<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElIcon, ElSwitch, ElSkeleton } from 'element-plus'
import { Refresh, InfoFilled } from '@element-plus/icons-vue'
import { useUserTime, TIME_STANDARD, type TimeKey } from '@/composables/useUserTime'
import { useCountUp } from '@/composables/useCountUp'
import { useUserStore } from '@/stores/user'

/**
 * 首页「义工时间」：总时长 + 三条进度条。
 *
 * 比三个圆环更克制，也更抗窄容器：数字从 0 缓动到目标值（easeOutExpo），
 * 进度条同步从 0 长到比例，进页面时看得到「长出来」的过程。
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
const { loading, error, exceed, base, shown, total, refresh } = useUserTime(() => id.value)

const order: TimeKey[] = ['on-campus', 'off-campus', 'social-practice']

const labels: Record<TimeKey, string> = {
  'on-campus': '校内义工',
  'off-campus': '校外义工',
  'social-practice': '社会实践'
}

const totalAnimated = useCountUp(() => total.value, 1100)
const animated = {
  'on-campus': useCountUp(() => shown.value['on-campus'], 1100),
  'off-campus': useCountUp(() => shown.value['off-campus'], 1100),
  'social-practice': useCountUp(() => shown.value['social-practice'], 1100)
}

function fmt(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function ratio(key: TimeKey) {
  const standard = TIME_STANDARD[key]
  return Math.min(100, Math.max(0, (shown.value[key] / standard) * 100))
}

function remain(key: TimeKey) {
  return Math.max(0, TIME_STANDARD[key] - shown.value[key])
}

const formulaOpen = ref(false)
</script>

<template>
  <div class="yt">
    <header class="yt__head">
      <div>
        <h2 class="yg2-card__title">{{ t('home.panels.time.title') }}</h2>
        <p class="yt__hint">累计 {{ fmt(totalAnimated) }} 小时，折算后计入</p>
      </div>
      <div class="yt__actions">
        <button class="yg2-icon-btn" type="button" title="刷新时长" aria-label="刷新时长" @click="refresh">
          <ElIcon><Refresh /></ElIcon>
        </button>
      </div>
    </header>

    <p v-if="error" class="yt__error">{{ error }}</p>
    <ElSkeleton v-else-if="loading" :rows="3" animated />

    <ul v-else class="yt__list">
      <li v-for="key in order" :key="key" class="yt__row">
        <div class="yt__rowhead">
          <span class="yt__label">{{ labels[key] }}</span>
          <span class="yt__value">
            <strong>{{ fmt(animated[key].value) }}</strong>
            <span class="yt__unit">/ {{ TIME_STANDARD[key] }} 小时</span>
          </span>
        </div>
        <div class="yt__track">
          <span class="yt__fill" :style="{ width: `${ratio(key)}%` }" />
        </div>
        <p class="yt__meta">
          <span v-if="remain(key) > 0">还差 {{ fmt(remain(key)) }} 小时</span>
          <span v-else class="yt__done">已达标</span>
        </p>
      </li>
    </ul>

    <footer class="yt__foot">
      <label class="yt__switch">
        <span>{{ t('home.panels.time.discount') }}</span>
        <ElSwitch v-model="exceed" size="small" aria-label="校内校外时长互补" />
      </label>
      <button class="yt__formula" type="button" @click="formulaOpen = true">
        <ElIcon><InfoFilled /></ElIcon>
        折算公示
      </button>
    </footer>

    <Teleport to="body">
      <div
        v-if="formulaOpen"
        class="yt-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="yt-formula-title"
        @click.self="formulaOpen = false"
        @keydown.esc="formulaOpen = false"
      >
        <div class="yt-modal__card">
          <h3 id="yt-formula-title" class="yt-modal__title">折算公示</h3>
          <p class="yt__formula-desc">
            达标线：校内 25 小时、校外 15 小时、社会实践 18 小时。打开「显示折算」后，超出的部分按下表互补：
          </p>
          <table class="yt__table">
            <thead>
              <tr><th>来源</th><th>超出部分</th><th>折算为</th></tr>
            </thead>
            <tbody>
              <tr><td>校内</td><td>超过 25 小时</td><td>每 3 小时折 1 小时校外</td></tr>
              <tr><td>校外</td><td>超过 15 小时</td><td>每 2 小时折 1 小时校内</td></tr>
            </tbody>
          </table>
          <p class="yt__formula-desc">
            折算结果四舍五入到整小时，只影响展示，不改变数据库里的原始时长。
          </p>
          <div class="yt-modal__foot">
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
.yt {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
  container-type: inline-size;
}

.yt__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.yt__hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.yt__actions {
  display: flex;
  gap: 6px;
}

.yt__error {
  margin: 0;
  font-size: 13px;
  color: var(--yg2-danger);
}

.yt__list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.yt__row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.yt__rowhead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.yt__label {
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.yt__value {
  font-variant-numeric: tabular-nums;
}

.yt__value strong {
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.yt__unit {
  margin-left: 4px;
  font-size: 11.5px;
  color: var(--yg2-text-tertiary);
}

.yt__track {
  position: relative;
  height: 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--yg2-text) 8%, transparent);
  overflow: hidden;
}

.yt__fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--yg2-accent) 78%, transparent),
    var(--yg2-accent)
  );
  transition: width 1100ms cubic-bezier(0.16, 0.84, 0.24, 1);
}

.yt__meta {
  margin: 0;
  font-size: 11.5px;
  color: var(--yg2-text-tertiary);
}

.yt__done {
  color: var(--yg2-success);
}

.yt__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--yg2-sep);
}

.yt__switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--yg2-text-secondary);
  cursor: pointer;
}

.yt__formula {
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

.yt__formula:hover {
  background: color-mix(in srgb, var(--yg2-accent) 18%, transparent);
}

.yt__formula-desc {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--yg2-text-secondary);
}

.yt__table {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 12.5px;
}

.yt__table th,
.yt__table td {
  padding: 8px 6px;
  text-align: left;
  border-bottom: 1px solid var(--yg2-sep);
}

.yt__table th {
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

@media (prefers-reduced-motion: reduce) {
  .yt__fill {
    transition: none;
  }
}

.yt-modal {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  animation: yt-fade 180ms var(--yg2-ease-out);
}

.yt-modal__card {
  width: min(440px, 100%);
  padding: 22px 22px 16px;
  border-radius: var(--yg2-r-26);
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  box-shadow: 0 30px 80px -24px rgba(0, 0, 0, 0.5);
  animation: yt-pop 260ms cubic-bezier(0.34, 1.32, 0.64, 1);
}

.yt-modal__title {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 600;
}

.yt-modal__foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

@keyframes yt-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes yt-pop {
  from { opacity: 0; transform: translateY(8px) scale(0.98); }
  to { opacity: 1; transform: none; }
}
</style>

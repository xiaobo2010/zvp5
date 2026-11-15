<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import HomeOverview from '@/components/home/HomeOverview.vue'
import { calculateHomeProgress } from '@/utils/homeArtwork'

const accounts = [
  { key: 'home-demo-003', name: '小禾', number: '演示账号 A' },
  { key: 'home-demo-001', name: '小川', number: '演示账号 B' },
  { key: 'home-demo-002', name: '小林', number: '演示账号 C' }
]
const accountIndex = ref(0)
const account = computed(() => accounts[accountIndex.value] ?? accounts[0]!)
const raw = reactive({ onCampus: 17.5, offCampus: 6, socialPractice: 12.6 })
const state = ref('ready')
const progress = computed(() =>
  ['loading', 'error'].includes(state.value) ? null : calculateHomeProgress(raw)
)
const error = computed(() =>
  state.value === 'error'
    ? '暂时无法取得时长，请重试。未读取到的数据不会显示为零。'
    : state.value === 'stale'
      ? '刷新失败，当前展示上次成功读取的时长。'
      : ''
)
const controls = ref<HTMLDetailsElement | null>(null)
const notice = ref('')
const originalTheme = document.documentElement.dataset.theme
const theme = ref(
  originalTheme === 'dark' ||
    (!originalTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)
    ? 'dark'
    : 'light'
)
watch(
  theme,
  (value) => {
    document.documentElement.dataset.theme = value
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  if (originalTheme === undefined) delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = originalTheme
})
function setHours(onCampus: number, offCampus: number, socialPractice: number) {
  Object.assign(raw, { onCampus, offCampus, socialPractice })
  state.value = 'ready'
}
function openControls() {
  if (!controls.value) return
  controls.value.open = true
  controls.value.scrollIntoView({ block: 'start' })
}
function refresh() {
  state.value = 'ready'
  notice.value = '演示数据已就绪；此页面不会请求后端。'
}
function showActivities() {
  notice.value = '正式首页的「参与活动」会打开活动列表；此处仅用于视觉测试。'
  openControls()
}
function scrollTop() {
  window.scrollTo({ top: 0 })
}
</script>

<template>
  <main class="yg-home-preview">
    <HomeOverview
      :name="account.name"
      :number="account.number"
      :user-key="account.key"
      :progress="progress"
      :loading="state === 'loading'"
      :error="error"
      @refresh="refresh"
      @details="openControls"
      @profile="openControls"
      @activities="showActivities"
    />
    <details ref="controls" class="yg-home-preview__controls">
      <summary>预览设置 · 账号、时长与状态</summary>
      <p>
        无后端测试。以约 123:238 的竖屏比例查看，例如 480 × 929 CSS
        像素。切换账号或刷新页面，账号对应的作品与揭示路径保持一致。
      </p>
      <div class="yg-home-preview__fields">
        <label class="yg-home-preview__field">
          演示账号
          <select v-model="accountIndex">
            <option v-for="(item, index) in accounts" :key="item.key" :value="index">
              {{ item.number }} · {{ item.name }}
            </option>
          </select>
        </label>
        <label class="yg-home-preview__field">
          数据状态
          <select v-model="state">
            <option value="ready">正常读取</option>
            <option value="loading">首次读取中</option>
            <option value="error">首次读取失败</option>
            <option value="stale">刷新失败（保留已知数据）</option>
          </select>
        </label>
        <label class="yg-home-preview__field">
          外观
          <select v-model="theme">
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </label>
      </div>
      <div class="yg-home-preview__presets" aria-label="时长预设">
        <button
          type="button"
          class="yg-btn yg-btn--secondary yg-btn--lg"
          @click="setHours(0, 0, 0)"
        >
          全部 0%
        </button>
        <button
          type="button"
          class="yg-btn yg-btn--secondary yg-btn--lg"
          @click="setHours(17.5, 6, 12.6)"
        >
          部分完成
        </button>
        <button
          type="button"
          class="yg-btn yg-btn--secondary yg-btn--lg"
          @click="setHours(25, 15, 18)"
        >
          全部 100%
        </button>
        <button
          type="button"
          class="yg-btn yg-btn--secondary yg-btn--lg"
          @click="setHours(34, 19, 21)"
        >
          超额与折算
        </button>
      </div>
      <div class="yg-home-preview__fields">
        <label class="yg-home-preview__field"
          >校内原始时长（小时）<input
            v-model.number="raw.onCampus"
            type="number"
            min="0"
            max="1000"
            step="0.1"
        /></label>
        <label class="yg-home-preview__field"
          >校外原始时长（小时）<input
            v-model.number="raw.offCampus"
            type="number"
            min="0"
            max="1000"
            step="0.1"
        /></label>
        <label class="yg-home-preview__field"
          >社会实践原始时长（小时）<input
            v-model.number="raw.socialPractice"
            type="number"
            min="0"
            max="1000"
            step="0.1"
        /></label>
      </div>
      <p class="yg-home-preview__notice" role="status">
        {{ notice || '这些调节只保存在当前页面内存，不写入真实账号。' }}
      </p>
    </details>
    <nav class="yg-home-preview__dock" aria-label="本地预览工具">
      <button type="button" class="yg-btn yg-btn--ghost yg-btn--lg" @click="scrollTop">首页</button>
      <button type="button" class="yg-btn yg-btn--ghost yg-btn--lg" @click="openControls">
        调节进度
      </button>
      <button
        type="button"
        class="yg-btn yg-btn--ghost yg-btn--lg"
        @click="theme = theme === 'dark' ? 'light' : 'dark'"
      >
        {{ theme === 'dark' ? '浅色' : '深色' }}
      </button>
    </nav>
  </main>
</template>

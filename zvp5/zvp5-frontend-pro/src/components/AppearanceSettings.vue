<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import {
  palettes,
  palettePreference,
  setPalettePreference,
  type PalettePreference,
} from '@/composables/appearance'
import {
  currentSiteTheme,
  loadAccountTheme,
  loadSiteThemes,
  readSiteTheme,
  saveSiteTheme,
  SITE_THEME_STORAGE_KEY,
  siteThemeDestination,
  switchesSiteOrigin,
  type SiteTheme,
} from '@/composables/siteTheme'

const catalog = ref<SiteTheme[]>([])
const catalogState = ref<'loading' | 'ready' | 'unavailable'>('loading')
const retrying = ref(false)
const themeList = ref<HTMLUListElement | null>(null)
const deviceTheme = ref(readSiteTheme())
const accountTheme = ref<string | null>(null)
const activeTheme = computed(() => currentSiteTheme(catalog.value))
const leavesOrigin = switchesSiteOrigin()
const busyTheme = ref('')
const themeFeedback = ref<{ message: string; success: boolean } | null>(null)
const paletteSaved = ref<boolean | null>(null)
let catalogRequest: AbortController | undefined
let accountRequest: AbortController | undefined
let mounted = false

function refreshDeviceTheme() {
  deviceTheme.value = readSiteTheme()
}

function syncDeviceTheme(event: StorageEvent) {
  if (event.key === SITE_THEME_STORAGE_KEY || event.key === null) refreshDeviceTheme()
}

async function loadCatalog(trigger?: EventTarget | null) {
  if (catalogRequest) return
  catalogState.value = 'loading'
  const request = new AbortController()
  catalogRequest = request
  const timeout = window.setTimeout(() => request.abort(), 6000)
  try {
    const themes = await loadSiteThemes(request.signal)
    if (!mounted) return
    const restoreFocus = trigger != null && document.activeElement === trigger
    catalog.value = themes
    catalogState.value = 'ready'
    if (restoreFocus) {
      await nextTick()
      const current = themeList.value?.querySelector<HTMLButtonElement>('[aria-current="true"]')
      const first = themeList.value?.querySelector<HTMLButtonElement>('button')
      const target = current ?? first
      target?.focus()
    }
  } catch {
    if (mounted) catalogState.value = 'unavailable'
  } finally {
    window.clearTimeout(timeout)
    catalogRequest = undefined
  }
}

async function retryCatalog(event: MouseEvent) {
  if (retrying.value) return
  retrying.value = true
  await loadCatalog(event.currentTarget)
  retrying.value = false
}

async function refreshAccountTheme() {
  accountRequest?.abort()
  const request = new AbortController()
  accountRequest = request
  const theme = await loadAccountTheme(request)
  if (accountRequest !== request) return
  accountRequest = undefined
  if (mounted) accountTheme.value = theme
}

async function chooseTheme(theme: SiteTheme) {
  if (busyTheme.value) return
  accountRequest?.abort()
  accountRequest = undefined
  busyTheme.value = theme.id
  themeFeedback.value = null

  const result = await saveSiteTheme(theme.id)
  if (!mounted) return
  if (result.local) deviceTheme.value = theme.id
  busyTheme.value = ''

  if (result.account === 'saved') {
    accountTheme.value = theme.id
    themeFeedback.value = { message: '默认主题已保存到账号。', success: true }
  } else if (result.local) {
    themeFeedback.value = {
      message: result.account === 'failed' ? '主题已保存在此浏览器，账号同步失败。' : '主题已保存在此浏览器。',
      success: result.account !== 'failed',
    }
  } else {
    themeFeedback.value = { message: '浏览器无法保存主题设置。', success: false }
  }

  if (theme.id !== activeTheme.value) window.location.assign(siteThemeDestination(theme))
}

function choosePalette(value: PalettePreference) {
  paletteSaved.value = setPalettePreference(value)
}

onMounted(() => {
  mounted = true
  window.addEventListener('storage', syncDeviceTheme)
  window.addEventListener('pageshow', refreshDeviceTheme)
  void loadCatalog()
  void refreshAccountTheme()
})

onUnmounted(() => {
  mounted = false
  catalogRequest?.abort()
  accountRequest?.abort()
  window.removeEventListener('storage', syncDeviceTheme)
  window.removeEventListener('pageshow', refreshDeviceTheme)
})
</script>

<template>
  <section
    id="appearance"
    class="appearance"
    aria-labelledby="appearance-title"
    :data-theme-catalog="catalogState"
  >
    <h2 id="appearance-title">外观设置</h2>

    <fieldset class="appearance__group" aria-describedby="appearance-theme-description">
      <legend>站点主题</legend>
      <p id="appearance-theme-description" class="appearance__description">
        切换主题会打开所选站点的首页，并在此浏览器保存选择。登录后也会尝试同步到账号。
        <template v-if="leavesOrigin">切换到 Canary 站点后可能需要重新登录。</template>
      </p>
      <ul v-if="catalogState === 'ready'" ref="themeList" class="appearance__themes" role="list">
        <li v-for="theme in catalog" :key="theme.id">
          <button
            type="button"
            class="appearance__theme"
            :aria-current="theme.id === activeTheme ? 'true' : undefined"
            :aria-disabled="busyTheme ? 'true' : undefined"
            @click="chooseTheme(theme)"
          >
            <span class="appearance__theme-heading">
              <strong>{{ theme.label }}</strong>
              <span v-if="theme.id === activeTheme" class="appearance__badge">当前主题</span>
              <span v-if="theme.id === accountTheme" class="appearance__badge appearance__badge--muted">账号默认</span>
              <span v-else-if="theme.id === deviceTheme" class="appearance__badge appearance__badge--muted">此浏览器已保存</span>
              <span v-if="theme.status === 'experimental'" class="appearance__badge appearance__badge--muted">试验版</span>
            </span>
            <small v-if="theme.description">{{ theme.description }}</small>
          </button>
        </li>
      </ul>
      <button
        v-if="catalogState !== 'ready' && (catalogState === 'unavailable' || retrying)"
        type="button"
        class="appearance__retry"
        :aria-disabled="retrying ? 'true' : undefined"
        @click="retryCatalog"
      >
        重新加载主题
      </button>
      <div class="appearance__status" role="status">
        <p v-if="catalogState === 'loading'">正在加载站点主题…</p>
        <p v-else-if="catalogState === 'unavailable'">当前使用 Canary Pro，暂时无法获取本站的主题目录。</p>
        <p v-else-if="busyTheme">正在保存主题选择…</p>
        <BubbleFeedback
          v-else-if="themeFeedback"
          :class="themeFeedback.success ? 'glass-feedback--success' : 'glass-feedback--warn'"
        >
          {{ themeFeedback.message }}
        </BubbleFeedback>
      </div>
    </fieldset>

    <fieldset class="appearance__group" aria-describedby="appearance-palette-description">
      <legend>Pro 配色</legend>
      <p id="appearance-palette-description" class="appearance__description">
        调整 Pro 的背景、卡片和导航色调。选择独立保存在此浏览器。
      </p>
      <div class="appearance__options">
        <label
          v-for="palette in palettes"
          :key="palette.id"
          class="appearance__option"
          :class="`palette-${palette.id}`"
        >
          <input type="radio" name="pro-palette" :value="palette.id" :checked="palettePreference === palette.id" @change="choosePalette(palette.id)" />
          <span class="appearance__swatch" aria-hidden="true"></span>
          <span>{{ palette.label }}</span>
          <small>{{ palette.description }}</small>
        </label>
      </div>
      <label class="appearance__auto">
        <input type="radio" name="pro-palette" value="auto" :checked="palettePreference === 'auto'" @change="choosePalette('auto')" />
        随本地时间自动切换
      </label>
      <div class="appearance__status" role="status">
        <BubbleFeedback
          v-if="paletteSaved !== null"
          :key="palettePreference"
          :class="paletteSaved ? 'glass-feedback--success' : 'glass-feedback--warn'"
        >
          {{ paletteSaved ? '配色已应用。' : '配色已应用于此页面。浏览器无法保存设置。' }}
        </BubbleFeedback>
      </div>
    </fieldset>
  </section>
</template>

<style scoped>
.appearance {
  min-inline-size: 0;
  display: grid;
  gap: var(--space-5);
  padding: clamp(16px, 4vw, 28px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background-color: var(--color-bg-elevated, Canvas);
  background-image: var(--surface-matte, none);
  box-shadow: var(--glass-shadow);
}

.appearance h2 {
  color: var(--color-text-primary);
  font-size: var(--font-size-title-3);
  font-weight: 650;
}

.appearance__group {
  min-inline-size: 0;
  border: 0;
}

.appearance__group:first-of-type {
  padding-block-end: var(--space-5);
  border-block-end: 1px solid var(--color-border-moderate);
}

.appearance__group legend {
  margin-block-end: var(--space-1);
  color: var(--color-text-primary);
  font-size: var(--font-size-headline);
  font-weight: 650;
}

.appearance__description,
.appearance__status {
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
}

.appearance__description {
  margin-block-end: var(--space-3);
}

.appearance__themes,
.appearance__options {
  display: grid;
  gap: var(--space-3);
}

.appearance__themes {
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 200px), 1fr));
  list-style: none;
}

.appearance__themes > li {
  min-inline-size: 0;
}

.appearance__options {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.appearance__theme,
.appearance__retry {
  min-block-size: 44px;
  padding: var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated, Canvas);
  color: var(--color-text-primary, CanvasText);
}

.appearance__theme {
  display: grid;
  align-content: start;
  gap: var(--space-1);
  inline-size: 100%;
  block-size: 100%;
  text-align: start;
  overflow-wrap: anywhere;
}

.appearance__theme-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-1) var(--space-2);
}

.appearance__theme small {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.appearance__badge {
  padding-inline: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--color-bg-canvas);
  color: var(--accent-ink, #175588);
  font-size: var(--font-size-caption);
}

.appearance__badge--muted {
  color: var(--color-text-secondary);
}

.appearance__theme[aria-current='true'],
.appearance__theme:hover,
.appearance__retry:hover {
  border-color: var(--accent-ink, #175588);
}

.appearance__theme[aria-current='true'] {
  background-image: var(--surface-matte, none);
}

.appearance [aria-disabled='true'] {
  cursor: wait;
}

.appearance__status > * {
  margin-block-start: var(--space-2);
}

.appearance__option {
  position: relative;
  display: grid;
  gap: 2px;
  padding: var(--space-2);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
}

.appearance__option:has(:checked) {
  border-color: var(--accent-ink);
  box-shadow: inset 0 0 0 1px var(--accent-ink);
}

.appearance__option input {
  position: absolute;
  inset-block-start: 16px;
  inset-inline-end: 16px;
}

.appearance input {
  accent-color: var(--accent-ink);
}

.appearance__swatch {
  min-height: 42px;
  border-radius: var(--radius-sm);
  background: linear-gradient(125deg, var(--palette-main), var(--palette-low) 52%, var(--palette-edge));
}

.appearance__option small {
  color: var(--color-text-secondary);
}

.appearance__auto {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 44px;
  width: fit-content;
  cursor: pointer;
}

@media (max-width: 540px) {
  .appearance__options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

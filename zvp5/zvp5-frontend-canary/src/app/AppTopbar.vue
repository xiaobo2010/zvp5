<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUiStore } from '@/shared/stores/ui'
import { useSessionStore } from '@/shared/stores/session'
import ZAvatar from '@/shared/ui/ZAvatar.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const route = useRoute()
const { t, locale } = useI18n()
const ui = useUiStore()
const session = useSessionStore()

const title = computed(() => {
  const key = route.meta.titleKey
  return typeof key === 'string' ? t(key) : t('common.appTagline')
})

function toggleLocale() {
  const next = locale.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  locale.value = next
  ui.setLocale(next)
}
</script>

<template>
  <header class="topbar">
    <div class="left">
      <RouterLink to="/" class="compact-brand" aria-label="ZVP5 首页">ZVP5</RouterLink>
      <h1 class="page-title">{{ title }}</h1>
    </div>

    <div class="right">
      <button type="button" class="chip" :aria-label="t('common.language')" @click="toggleLocale">
        {{ locale === 'zh-CN' ? '中' : 'EN' }}
      </button>
      <button
        type="button"
        class="chip icon"
        :aria-label="t('common.theme')"
        @click="ui.toggleTheme()"
      >
        <ZIcon :name="ui.isDark ? 'sun' : 'moon'" :size="17" />
      </button>
      <RouterLink to="/me" class="avatar-link" :aria-label="t('nav.profile')">
        <ZAvatar :name="session.displayName" :size="34" />
      </RouterLink>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 25;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  height: var(--topbar-height);
  padding: 0 var(--gutter);
  background: color-mix(in srgb, var(--bg) 82%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  backdrop-filter: blur(18px) saturate(140%);
}

.left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.compact-brand {
  display: none;
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink);
  text-decoration: none;
}

.page-title {
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--ink-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.right {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.chip {
  display: grid;
  place-items: center;
  min-width: 34px;
  height: 34px;
  padding: 0 0.5rem;
  border-radius: 50%;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ink-muted);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.chip:hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--ink);
}

.avatar-link {
  display: inline-flex;
  margin-left: 0.2rem;
  border-radius: 50%;
  transition: transform var(--dur-fast) var(--ease-out);
}

.avatar-link:hover {
  transform: translateY(-1px);
}

@media (max-width: 1080px) {
  .compact-brand {
    display: inline;
  }

  .page-title {
    display: none;
  }

  .topbar {
    height: 58px;
  }
}
</style>

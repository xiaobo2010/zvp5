<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import { useNavItems, useRoleLabel } from '@/app/navigation'
import ZAvatar from '@/shared/ui/ZAvatar.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZModal from '@/shared/ui/ZModal.vue'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const ui = useUiStore()
const navItems = useNavItems()
const roleLabel = useRoleLabel()
const confirmOpen = ref(false)

async function signOut() {
  await session.signOut()
  confirmOpen.value = false
  router.push('/login')
}
</script>

<template>
  <aside class="rail glass">
    <RouterLink to="/" class="brand" aria-label="ZVP5 首页">
      <span class="mark" aria-hidden="true">Z</span>
      <span class="wordmark">
        <strong>ZVP5</strong>
        <small>{{ t('common.appTagline') }}</small>
      </span>
    </RouterLink>

    <nav class="nav" aria-label="主导航">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="nav-item"
        active-class="is-active"
        :exact-active-class="item.to === '/' ? 'is-active' : undefined"
      >
        <ZIcon :name="item.icon" :size="19" />
        <span>{{ t(item.labelKey) }}</span>
      </RouterLink>
    </nav>

    <div class="foot">
      <button type="button" class="theme" :aria-label="t('common.theme')" @click="ui.toggleTheme()">
        <ZIcon :name="ui.isDark ? 'sun' : 'moon'" :size="18" />
        <span>{{ ui.isDark ? t('common.light') : t('common.dark') }}</span>
      </button>

      <RouterLink to="/me" class="identity">
        <ZAvatar :name="session.displayName" :size="38" />
        <span class="who">
          <strong>{{ session.displayName || '—' }}</strong>
          <small>{{ t(roleLabel) }}</small>
        </span>
      </RouterLink>

      <ZButton variant="quiet" size="sm" icon="logout" @click="confirmOpen = true">
        {{ t('common.logout') }}
      </ZButton>
    </div>

    <ZModal v-model="confirmOpen" :title="t('common.logout')" size="sm">
      <p class="confirm-text">{{ t('profile.security') }}</p>
      <template #footer>
        <ZButton variant="ghost" @click="confirmOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" @click="signOut">{{ t('common.logout') }}</ZButton>
      </template>
    </ZModal>
  </aside>
</template>

<style scoped>
.rail {
  position: fixed;
  inset: 12px auto 12px 12px;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: calc(var(--rail-width) - 12px);
  padding: var(--space-5) var(--space-3);
  border-radius: var(--radius-xl);
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.35rem 0.65rem 1.4rem;
  color: var(--ink);
  text-decoration: none;
}

.mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: 12px;
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  color: #fffdf8;
  background: linear-gradient(145deg, #c9973f, #8c5a12);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), var(--shadow-sm);
}

.wordmark {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.wordmark strong {
  font-family: var(--font-display);
  font-size: 1.05rem;
  letter-spacing: 0.02em;
}

.wordmark small {
  font-size: var(--text-2xs);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: 1;
  overflow: auto;
  padding-top: var(--space-2);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.72rem;
  padding: 0.62rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--ink-muted);
  font-size: var(--text-sm);
  font-weight: 550;
  text-decoration: none;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out);
}

.nav-item:hover {
  color: var(--ink);
  background: color-mix(in srgb, var(--ink) 5%, transparent);
}

.nav-item.is-active {
  color: var(--accent-ink);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}

.nav-item.is-active :deep(svg) {
  stroke-width: 1.9;
}

.foot {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding-top: var(--space-3);
  border-top: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
}

.theme {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--ink-muted);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.theme:hover {
  background: color-mix(in srgb, var(--ink) 5%, transparent);
  color: var(--ink);
}

.identity {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.45rem 0.55rem;
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--dur-fast) var(--ease-out);
}

.identity:hover {
  background: color-mix(in srgb, var(--ink) 5%, transparent);
}

.who {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.3;
}

.who strong {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.who small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.confirm-text {
  font-size: var(--text-sm);
  color: var(--ink-muted);
}

@media (max-width: 1080px) {
  .rail {
    display: none;
  }
}
</style>

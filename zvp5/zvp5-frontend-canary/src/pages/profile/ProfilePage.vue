<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore, type LocaleCode, type ThemeMode } from '@/shared/stores/ui'
import { fetchGroup } from '@/shared/api/domain'
import ZAvatar from '@/shared/ui/ZAvatar.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZModal from '@/shared/ui/ZModal.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t, locale } = useI18n()
const router = useRouter()
const session = useSessionStore()
const ui = useUiStore()

const className = ref('')
const confirmOpen = ref(false)

const themeOptions = computed(() => [
  { value: 'system', label: t('common.system') },
  { value: 'light', label: t('common.light') },
  { value: 'dark', label: t('common.dark') }
])

const localeOptions = computed(() => [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' }
])

const roles = computed(() => session.roles.filter((role) => role !== 'system'))

function setTheme(value: string) {
  ui.setTheme(value as ThemeMode)
}

function setLanguage(value: string) {
  const next = value as LocaleCode
  locale.value = next
  ui.setLocale(next)
}

async function signOut() {
  await session.signOut()
  confirmOpen.value = false
  router.push('/login')
}

onMounted(async () => {
  const classId = session.profile?.class_id
  if (!classId) return
  try {
    const group = await fetchGroup(classId)
    className.value = group.name
  } catch {
    className.value = ''
  }
})
</script>

<template>
  <div class="page">
    <section class="identity">
      <ZAvatar :name="session.displayName" :size="76" />
      <div class="identity-body">
        <h1 class="name">{{ session.displayName || '—' }}</h1>
        <p class="meta">
          <span class="num">{{ session.profile?.id || '—' }}</span>
          <template v-if="className">
            <span class="sep">·</span>
            <span>{{ className }}</span>
          </template>
        </p>
        <div class="roles">
          <ZBadge v-for="role in roles" :key="role" tone="accent">{{ t(`role.${role}`) }}</ZBadge>
        </div>
      </div>
      <div class="identity-action">
        <ZButton variant="ghost" size="sm" icon="logout" @click="confirmOpen = true">
          {{ t('common.logout') }}
        </ZButton>
      </div>
    </section>

    <div class="grid">
      <ZCard class="setting">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.appearance') }}</h2>
          <ZIcon :name="ui.isDark ? 'moon' : 'sun'" :size="18" />
        </div>
        <p class="setting-desc">
          {{ locale === 'zh-CN' ? '浅色与深色分别调校，不只是颜色反转。' : 'Light and dark are tuned separately — not a color inversion.' }}
        </p>
        <ZSegmented :model-value="ui.themeMode" :options="themeOptions" @update:model-value="setTheme" />
      </ZCard>

      <ZCard class="setting">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.language') }}</h2>
          <ZIcon name="book" :size="18" />
        </div>
        <p class="setting-desc">
          {{ locale === 'zh-CN' ? '界面文案即时切换，无需刷新。' : 'Interface copy switches instantly, no reload.' }}
        </p>
        <ZSegmented :model-value="locale" :options="localeOptions" @update:model-value="setLanguage" />
      </ZCard>

      <ZCard class="setting wide">
        <div class="setting-head">
          <h2 class="setting-title">{{ t('profile.security') }}</h2>
          <ZIcon name="shield" :size="18" />
        </div>
        <ul class="security">
          <li class="security-row">
            <div>
              <strong>{{ locale === 'zh-CN' ? '修改密码' : 'Change password' }}</strong>
              <small>{{ locale === 'zh-CN' ? '修改后其他设备会自动退出。' : 'Other devices will be signed out.' }}</small>
            </div>
            <ZBadge tone="neutral">{{ t('common.soon') }}</ZBadge>
          </li>
          <li class="security-row">
            <div>
              <strong>{{ locale === 'zh-CN' ? '动态验证码' : 'TOTP' }}</strong>
              <small>{{ locale === 'zh-CN' ? '绑定身份验证器，提升登录安全。' : 'Bind an authenticator for stronger sign-in.' }}</small>
            </div>
            <ZBadge tone="neutral">{{ t('common.soon') }}</ZBadge>
          </li>
          <li class="security-row">
            <div>
              <strong>{{ locale === 'zh-CN' ? '主设备' : 'Primary device' }}</strong>
              <small>{{ locale === 'zh-CN' ? '查看和重置当前账号的主设备绑定。' : 'Review and reset the primary device binding.' }}</small>
            </div>
            <ZBadge tone="neutral">{{ t('common.soon') }}</ZBadge>
          </li>
        </ul>
        <p class="note">
          {{ locale === 'zh-CN' ? '这些能力将按计划在后续阶段接入真实接口，当前不做伪实现。' : 'These will be wired to real endpoints in a later stage — no fake implementations.' }}
        </p>
      </ZCard>

      <ZCard class="setting wide">
        <div class="setting-head">
          <h2 class="setting-title">{{ locale === 'zh-CN' ? '关于' : 'About' }}</h2>
          <ZIcon name="sparkles" :size="18" />
        </div>
        <dl class="about">
          <div>
            <dt>{{ t('profile.version') }}</dt>
            <dd>Canary 0.1.0</dd>
          </div>
          <div>
            <dt>{{ locale === 'zh-CN' ? '构建' : 'Build' }}</dt>
            <dd>Vue 3 · Vite</dd>
          </div>
          <div>
            <dt>{{ locale === 'zh-CN' ? '设计语言' : 'Design' }}</dt>
            <dd>Warm · Editorial · Glass</dd>
          </div>
        </dl>
      </ZCard>
    </div>

    <ZModal v-model="confirmOpen" :title="t('common.logout')" size="sm">
      <p class="confirm-text">
        {{ locale === 'zh-CN' ? '退出后需要重新登录才能继续使用。' : 'You will need to sign in again to continue.' }}
      </p>
      <template #footer>
        <ZButton variant="ghost" @click="confirmOpen = false">{{ t('common.cancel') }}</ZButton>
        <ZButton variant="primary" @click="signOut">{{ t('common.logout') }}</ZButton>
      </template>
    </ZModal>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
  max-width: 960px;
}

.identity {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: clamp(1.4rem, 1rem + 2vw, 2.2rem);
  border-radius: var(--radius-xl);
  border: 1px solid var(--line);
  background:
    radial-gradient(32rem 20rem at 90% 0%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 62%),
    var(--surface);
  box-shadow: var(--shadow-sm);
}

.identity-body {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  flex: 1;
  min-width: 0;
}

.name {
  font-size: var(--text-2xl);
  font-weight: 500;
}

.meta {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  white-space: nowrap;
}

.identity-action {
  flex: none;
}

.sep {
  opacity: 0.6;
}

.roles {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.2rem;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.setting {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.setting.wide {
  grid-column: 1 / -1;
}

.setting-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--ink-muted);
}

.setting-title {
  font-family: var(--font-sans);
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
}

.setting-desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.6;
}

.security {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.security-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) 0;
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.security-row div {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.security-row strong {
  font-size: var(--text-sm);
  font-weight: 550;
}

.security-row small {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.note {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.6;
}

.about {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-5);
  margin: 0;
}

.about div {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-right: var(--space-4);
  border-right: 1px solid color-mix(in srgb, var(--line) 45%, transparent);
}

.about div:last-child {
  border-right: 0;
  padding-right: 0;
}

.about dt {
  font-size: var(--text-2xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.about dd {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.confirm-text {
  font-size: var(--text-sm);
  color: var(--ink-muted);
}

@media (max-width: 760px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .identity {
    flex-wrap: wrap;
  }

  .identity-body {
    flex-basis: calc(100% - 76px - var(--space-5));
  }

  .about {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }

  .about div {
    border-right: 0;
    padding-right: 0;
    padding-bottom: var(--space-2);
    border-bottom: 1px solid color-mix(in srgb, var(--line) 45%, transparent);
  }

  .about div:last-child {
    border-bottom: 0;
    padding-bottom: 0;
  }
}
</style>

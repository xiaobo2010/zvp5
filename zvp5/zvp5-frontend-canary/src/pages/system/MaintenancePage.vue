<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { request, unwrap } from '@/shared/http/client'
import { useSessionStore } from '@/shared/stores/session'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t, locale } = useI18n()
const router = useRouter()
const session = useSessionStore()

interface MaintenanceState {
  enabled: boolean
  since?: string | null
  announcement?: string | null
  admin?: boolean
}

const state = ref<MaintenanceState | null>(null)
const checking = ref(false)
const note = ref('')

async function check() {
  checking.value = true
  note.value = ''
  try {
    const payload = await request<unknown>({ url: '/system/maintenance', method: 'GET' })
    const data = unwrap<MaintenanceState>(payload)
    state.value = data
    if (!data.enabled) {
      note.value = locale.value === 'zh-CN' ? '维护已结束，正在返回。' : 'Maintenance finished. Returning.'
      window.setTimeout(() => {
        void router.replace(session.authenticated ? '/' : '/login')
      }, 700)
    }
  } catch {
    note.value = locale.value === 'zh-CN' ? '暂时无法获取维护状态。' : 'Could not read maintenance state.'
  } finally {
    checking.value = false
  }
}

void check()
</script>

<template>
  <div class="page">
    <div class="panel">
      <div class="glyph" aria-hidden="true">
        <ZIcon name="server" :size="26" />
      </div>
      <p class="eyebrow">ZVP5 · Maintenance</p>
      <h1 class="title">{{ t('maintenance.title') }}</h1>
      <p class="desc">{{ t('maintenance.desc') }}</p>

      <ZCard v-if="state?.announcement" tone="sunken" class="announcement">
        <p>{{ state.announcement }}</p>
      </ZCard>

      <p v-if="note" class="note">{{ note }}</p>

      <div class="actions">
        <ZButton variant="primary" icon="refresh" :loading="checking" @click="check">
          {{ t('maintenance.retry') }}
        </ZButton>
        <RouterLink v-if="session.isAdmin" to="/server">
          <ZButton variant="ghost" icon="shield">{{ t('nav.server') }}</ZButton>
        </RouterLink>
      </div>

      <p class="foot">Zhenhai Volunteer Platform · Canary</p>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  place-items: center;
  min-height: 100dvh;
  padding: var(--space-6);
}

.panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  width: 100%;
  max-width: 30rem;
  padding: clamp(2rem, 1.4rem + 3vw, 3.5rem);
  text-align: center;
  border-radius: var(--radius-2xl);
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-md);
}

.glyph {
  display: grid;
  place-items: center;
  width: 62px;
  height: 62px;
  margin-bottom: var(--space-2);
  border-radius: 50%;
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
}

.title {
  font-size: var(--text-2xl);
  font-weight: 500;
}

.desc {
  max-width: 34ch;
  color: var(--ink-muted);
  line-height: 1.7;
}

.announcement {
  width: 100%;
  text-align: left;
  font-size: var(--text-sm);
  color: var(--ink-soft);
}

.note {
  font-size: var(--text-sm);
  color: var(--c-success);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.foot {
  margin-top: var(--space-6);
  font-size: var(--text-2xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-faint);
}
</style>

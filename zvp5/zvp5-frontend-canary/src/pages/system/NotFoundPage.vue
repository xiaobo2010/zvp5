<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ZButton from '@/shared/ui/ZButton.vue'

const { t } = useI18n()
</script>

<template>
  <div class="page">
    <p class="code num">404</p>
    <h1 class="title">{{ t('notFound.title') }}</h1>
    <p class="desc">{{ t('notFound.desc') }}</p>
    <RouterLink to="/">
      <ZButton variant="primary" icon="chevron-left">{{ t('notFound.back') }}</ZButton>
    </RouterLink>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  min-height: 100dvh;
  padding: var(--space-6);
  text-align: center;
}

.code {
  font-family: var(--font-display);
  font-size: clamp(4rem, 10vw, 7rem);
  line-height: 1;
  color: color-mix(in srgb, var(--accent) 70%, var(--ink));
  opacity: 0.7;
}

.title {
  font-size: var(--text-2xl);
  font-weight: 500;
}

.desc {
  max-width: 32ch;
  color: var(--ink-muted);
  margin-bottom: var(--space-3);
}
</style>

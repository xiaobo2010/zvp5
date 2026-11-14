<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ElButton } from 'element-plus'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const router = useRouter()
</script>

<template>
  <div class="yg2-page notfound-page">
    <div class="notfound-card">
      <p class="notfound-code">404</p>
      <h1 class="yg2-page-header__title">{{ t('nav.pages.NotFound.title') }}</h1>
      <p class="yg2-page-header__subtitle">{{ t('nav.pages.NotFound.description') }}</p>
      <ElButton type="primary" round @click="router.push('/')">
        {{ t('nav.pages.NotFound.action.back') }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.notfound-page {
  display: grid;
  place-items: center;
  min-height: calc(100dvh - var(--yg2-topbar-h) - 48px);
}

.notfound-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 40px 32px;
  text-align: center;
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-26);
  box-shadow: var(--yg2-shadow-ambient);
  max-width: 420px;
}

.notfound-code {
  margin: 0;
  font-size: 64px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.04em;
  color: var(--yg2-accent);
}

.notfound-card .yg2-page-header__subtitle {
  margin-bottom: 8px;
}
</style>

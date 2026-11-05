<script setup lang="ts">
import { ElCollapse, ElCollapseItem } from 'element-plus'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import { onMounted, ref } from 'vue'

const { t } = useI18n()
const header = useHeaderStore()
const version = ref('—')

onMounted(async () => {
  const value = await api.version.getVersion()
  if (value) version.value = value
})

header.setHeader(t('nav.about'))
</script>

<template>
  <div class="yg-v2-page yg-about">
    <section class="yg-card yg-about__card" aria-labelledby="about-title">
      <div class="yg-card__body">
        <img class="yg-about__logo" src="/pwa-192x192.png" alt="义工平台 Logo" />
        <h1 id="about-title" class="yg-about__name">镇海中学义工平台</h1>
        <p class="yg-about__version">版本 {{ version }}</p>
        <p class="yg-about__version">{{ t('about.bio') }}</p>
      </div>
    </section>

    <section class="yg-card yg-about__license" aria-labelledby="about-license-title">
      <div class="yg-card__header">
        <h2 id="about-license-title" class="yg-card__title">关于本项目</h2>
      </div>
      <div class="yg-card__body">
        <p class="yg-about__version">
          © 2018–2026 {{ t('about.footer') }} · {{ t('about.license') }}
        </p>
        <ElCollapse>
          <ElCollapseItem :title="t('about.repository.license')" name="license">
            <p>{{ t('about.license') }}</p>
          </ElCollapseItem>
          <ElCollapseItem :title="t('about.privacy.title')" name="privacy">
            <p>{{ t('about.privacy.content') }}</p>
          </ElCollapseItem>
        </ElCollapse>
      </div>
    </section>
  </div>
</template>

<style scoped>
.yg-about__card {
  text-align: center;
}

.yg-about__card .yg-card__body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-7) var(--yg-space-5);
}

.yg-about__logo {
  width: 64px;
  height: 64px;
  border-radius: var(--yg-radius-lg);
}
</style>

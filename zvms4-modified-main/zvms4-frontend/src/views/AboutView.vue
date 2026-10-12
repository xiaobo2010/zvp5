<script setup lang="ts">
import { ElResult, ElButton, ElCard, ElPageHeader } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import router from '@/router'
import { APP_VERSION } from '@/data/changelog'

const { t } = useI18n()
const header = useHeaderStore()
header.setHeader(t('nav.about'))
</script>

<template>
  <div class="p-4">
    <ElPageHeader :icon="ArrowLeft" @back="router.back()" class="py-4">
      <template #content>
        <h1 class="text-xl">{{ t('nav.about') }}</h1>
      </template>
    </ElPageHeader>
    <ElCard shadow="never">
      <ElResult
        icon="info"
        title="义工平台 v4"
        :sub-title="APP_VERSION"
      >
        <template #icon>
            <img :src="`/favicon.png?v=${APP_VERSION}`" alt="logo" style="width: 128px; height: 128px; border-radius: .75rem;"/>
        </template>
        <template #extra>
          <div class="flex flex-col items-center gap-3">
            <ElButton text bg type="primary" @click="router.push('/changelog')">
              查看更新日志
            </ElButton>
            <div style="opacity: .5;">
              &copy; 2018-2026 | {{ t('about.footer') }} | {{ t('about.license') }}
            </div>
          </div>
        </template>
      </ElResult>
    </ElCard>
  </div>
</template>

<style scoped></style>

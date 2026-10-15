<script setup lang="ts">
import { ElResult, ElButton, ElCard, ElPageHeader } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import router from '@/router'
import api from '@/api'
import { ref, onMounted } from 'vue'

const { t } = useI18n()
const header = useHeaderStore()
const version = ref('—')

onMounted(async () => {
  const value = await api.version.getVersion()
  if (value) {
    version.value = value
  }
})

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
        title="镇海中学义工平台"
        :sub-title="version"
      >
        <template #icon>
            <img :src="`/favicon.png?v=${version}`" alt="logo" style="width: 128px; height: 128px; border-radius: .75rem;"/>
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

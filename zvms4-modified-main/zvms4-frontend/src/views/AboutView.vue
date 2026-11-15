<script setup lang="ts">
import { ElResult, ElButton, ElCard, ElPageHeader } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import router from '@/router'
import api from '@/api'
import { ref, onMounted } from 'vue'
import GlassBackButton from '@/ui/glass/GlassBackButton.vue'

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
  <div class="yg2-page about-page">
    <GlassBackButton class="about-back" @click="router.back()" />

    <div class="about-card yg2-glass-surface">
      <ElResult icon="info" title="镇海中学义工平台" :sub-title="version">
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
    </div>
  </div>
</template>

<style scoped>
.about-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 760px;
  margin: 0 auto;
}



/* 材质来自 .yg2-glass-surface（v2/glass.css），这里只保留尺寸 */
.about-card {
  padding: 24px;
  border-radius: var(--yg2-r-26);
}

.about-card :deep(.el-result__title) {
  margin-top: 4px;
}

.about-card :deep(.el-result__title p) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--yg2-text);
}

.about-card :deep(.el-result__subtitle p) {
  color: var(--yg2-text-tertiary);
}

.about-back {
  align-self: flex-start;
}
</style>

<script setup lang="ts">
import { ElCard, ElTimeline, ElTimelineItem } from 'element-plus'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { changelog } from '@/data/changelog'

const { t } = useI18n()
const header = useHeaderStore()
header.setHeader(t('nav.changelog'))
</script>

<template>
  <div class="p-4">
    <ElCard shadow="never">
      <ElTimeline>
        <ElTimelineItem
          v-for="entry in changelog"
          :key="entry.version"
          :timestamp="entry.date"
          placement="top"
        >
          <h3 class="text-lg font-bold mb-2">{{ entry.version }}</h3>
          <ul class="list-disc pl-5 text-sm space-y-1">
            <li v-for="(item, idx) in entry.items" :key="idx">{{ item }}</li>
          </ul>
        </ElTimelineItem>
      </ElTimeline>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t, locale } = useI18n()
const route = useRoute()

const moduleName = computed(() => String(route.name ?? 'manage'))

const config = computed(() => {
  const zh = locale.value === 'zh-CN'
  if (moduleName.value === 'server') {
    return {
      icon: 'server',
      title: zh ? '运维管理' : 'Operations',
      desc: zh
        ? '数据库、文件、Web SSH 与维护模式将在这里重建。'
        : 'Database, files, Web SSH and maintenance controls will be rebuilt here.',
      items: zh
        ? ['系统状态与维护模式', '数据库监控与内容管理', '文件管理与 Web SSH', '双人 TOTP 与操作审计']
        : ['Status and maintenance mode', 'Database monitoring and content', 'Files and Web SSH', 'Dual TOTP and audit']
    }
  }
  if (moduleName.value === 'ai') {
    return {
      icon: 'sparkles',
      title: zh ? 'ZVAgent' : 'ZVAgent',
      desc: zh
        ? '流式对话、智能体、配额与模型管理将在这里重建。'
        : 'Streaming chat, agents, quota and model management will be rebuilt here.',
      items: zh
        ? ['对话与流式回复', '智能体 run 与工具调用', 'OCR 与场景分析', '配额、Key 与调用日志']
        : ['Chat and streaming', 'Agent runs and tools', 'OCR and scene analysis', 'Quota, keys and logs']
    }
  }
  return {
    icon: 'shield',
    title: zh ? '管理中心' : 'Management',
    desc: zh
      ? '用户、班级、活动审核、通知与审计将在这里重建。'
      : 'Users, classes, activity review, notices and audit will be rebuilt here.',
    items: zh
      ? ['用户与角色', '班级与权限组', '活动审核与义工登记', '通知、封禁与审计日志']
      : ['Users and roles', 'Classes and permission groups', 'Activity review and registration', 'Notices, bans and audit logs']
  }
})
</script>

<template>
  <div class="page">
    <ZCard class="panel" padding="lg">
      <div class="top">
        <span class="glyph"><ZIcon :name="config.icon" :size="24" /></span>
        <ZBadge tone="accent">{{ t('common.soon') }}</ZBadge>
      </div>
      <h1 class="title">{{ config.title }}</h1>
      <p class="desc">{{ config.desc }}</p>

      <ul class="items">
        <li v-for="item in config.items" :key="item">
          <ZIcon name="clock" :size="15" />
          <span>{{ item }}</span>
        </li>
      </ul>

      <p class="honest">
        {{
          locale === 'zh-CN'
            ? '本阶段不接入真实接口，也不会用假数据填充页面。'
            : 'This stage does not call real endpoints, and will not fill the page with fake data.'
        }}
      </p>

      <RouterLink to="/">
        <ZButton variant="secondary" icon="chevron-left">{{ t('notFound.back') }}</ZButton>
      </RouterLink>
    </ZCard>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  place-items: center;
  min-height: 62dvh;
}

.panel {
  width: 100%;
  max-width: 42rem;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.glyph {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border-radius: var(--radius-md);
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
}

.title {
  font-size: var(--text-2xl);
  font-weight: 500;
}

.desc {
  color: var(--ink-muted);
  line-height: 1.7;
}

.items {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  margin: var(--space-2) 0;
  padding: 0;
  list-style: none;
}

.items li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
  font-size: var(--text-sm);
  color: var(--ink-soft);
}

.items :deep(svg) {
  color: var(--ink-faint);
  flex: none;
}

.honest {
  font-size: var(--text-xs);
  color: var(--ink-faint);
  line-height: 1.6;
  padding-top: var(--space-2);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

@media (max-width: 560px) {
  .items {
    grid-template-columns: 1fr;
  }
}
</style>

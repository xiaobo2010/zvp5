<script setup lang="ts">
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const entries = [
  {
    key: 'add',
    title: '添加义工',
    description: '把活动里还没有的成员加进来，填写学号、时长和义工类型。',
    action: '开始添加',
    to: '/volunteers/create/add',
    icon: 'add'
  },
  {
    key: 'modify',
    title: '修改义工',
    description: '查找活动里已录入的成员，按新的时长和类型覆盖原记录。',
    action: '开始修改',
    to: '/volunteers/create/modify',
    icon: 'edit'
  },
  {
    key: 'new',
    title: '创建义工',
    description: '新建一场志愿活动，填写名称、类型和说明，提交审核后再登记成员。',
    action: '去创建',
    to: '/volunteers/create/new',
    icon: 'create'
  }
] as const

function goBack() {
  router.back()
}
</script>

<template>
  <div class="register-entry">
    <div class="register-entry__container">
      <header class="register-entry__header surface-enter" style="--i: 0">
        <button type="button" class="register-entry__back" aria-label="返回" @click="goBack">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div>
          <p class="register-entry__eyebrow">义工登记</p>
          <h1 class="register-entry__title">添加 / 修改 / 创建</h1>
          <p class="register-entry__subtitle">
            先选择要做的操作。添加与修改进入同一个登记工作台，创建义工则新建活动。
          </p>
        </div>
      </header>

      <div class="register-entry__choices">
        <RouterLink
          v-for="(entry, index) in entries"
          :key="entry.key"
          :to="entry.to"
          class="register-entry__choice surface-enter"
          :class="`register-entry__choice--${entry.key}`"
          :style="{ '--i': index + 1 }"
        >
          <span class="register-entry__icon" aria-hidden="true">
            <svg v-if="entry.icon === 'add'" width="26" height="26" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="8" r="3.2" stroke="currentColor" stroke-width="1.7"/>
              <path d="M5.5 19C5.5 15.7 8.2 14 12 14C15.8 14 18.5 15.7 18.5 19" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
              <path d="M19.2 8.5V13.3M16.8 10.9H21.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
            </svg>
            <svg v-else-if="entry.icon === 'edit'" width="26" height="26" viewBox="0 0 24 24" fill="none">
              <path d="M4.5 19.5L8.4 18.7L19.2 7.9C19.9 7.2 19.9 6.1 19.2 5.4L18.6 4.8C17.9 4.1 16.8 4.1 16.1 4.8L5.3 15.6L4.5 19.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
              <path d="M14.8 6.1L17.9 9.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
            </svg>
            <svg v-else width="26" height="26" viewBox="0 0 24 24" fill="none">
              <rect x="4" y="4.5" width="16" height="15" rx="3.2" stroke="currentColor" stroke-width="1.7"/>
              <path d="M8 9.3H16M8 13H13.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
              <path d="M15.5 16.5V19M14 17.75H17" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
            </svg>
          </span>
          <span class="register-entry__body">
            <span class="register-entry__choice-title">{{ entry.title }}</span>
            <span class="register-entry__description">{{ entry.description }}</span>
          </span>
          <span class="register-entry__action">
            {{ entry.action }}
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M9 5L16 12L9 19" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
        </RouterLink>
      </div>

      <BubbleFeedback class="register-entry__hint">
        修改义工只覆盖原成员的时长与类型，不会影响其他人的记录；创建义工不受此限制。
      </BubbleFeedback>
    </div>
  </div>
</template>

<style scoped>
.register-entry {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.register-entry__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.register-entry__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-8);
}

.register-entry__back {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.84);
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.8), rgba(232, 242, 252, 0.56));
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  color: var(--color-text-secondary);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    0 2px 8px rgba(18, 48, 82, 0.08);
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth);
}

.register-entry__back:hover {
  color: var(--color-text-primary);
}

.register-entry__back:active {
  transform: scale(0.94);
}

.register-entry__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.register-entry__eyebrow {
  margin: 0 0 var(--space-1);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.register-entry__title {
  margin: 0;
  font-size: var(--font-size-title-2);
  line-height: var(--line-height-tight);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
}

.register-entry__subtitle {
  max-width: 48rem;
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
}

.register-entry__choices {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-6);
}

.register-entry__choice {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-height: 250px;
  padding: var(--space-6);
  color: inherit;
  text-decoration: none;
  background: var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  overflow: hidden;
  isolation: isolate;
  transition:
    transform var(--duration-base) var(--ease-spring),
    box-shadow var(--duration-base) var(--ease-smooth),
    border-color var(--duration-base) var(--ease-smooth);
}

.register-entry__choice::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  background: radial-gradient(circle at 12% 0%, var(--entry-glow, transparent) 0%, transparent 62%);
  opacity: 0.85;
  pointer-events: none;
}

.register-entry__choice--add {
  --entry-glow: rgba(0, 113, 227, 0.2);
}

.register-entry__choice--modify {
  --entry-glow: rgba(20, 184, 166, 0.2);
}

.register-entry__choice--new {
  --entry-glow: rgba(245, 158, 11, 0.18);
}

.register-entry__choice:hover {
  transform: translateY(-3px);
  border-color: rgba(255, 255, 255, 0.95);
  box-shadow:
    var(--glass-shadow),
    0 16px 36px rgba(18, 48, 82, 0.12);
}

.register-entry__choice:active {
  transform: translateY(-1px) scale(0.995);
}

.register-entry__choice:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
}

.register-entry__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 54px;
  height: 54px;
  border-radius: 18px;
  color: var(--color-brand);
  background: linear-gradient(135deg, rgba(0, 113, 227, 0.16), rgba(255, 255, 255, 0.58));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.74);
}

.register-entry__choice--modify .register-entry__icon {
  color: rgb(13, 128, 116);
  background: linear-gradient(135deg, rgba(20, 184, 166, 0.16), rgba(255, 255, 255, 0.58));
}

.register-entry__choice--new .register-entry__icon {
  color: rgb(180, 83, 9);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(255, 255, 255, 0.58));
}

.register-entry__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-2);
}

.register-entry__choice-title {
  font-size: var(--font-size-title-3);
  font-weight: 650;
  line-height: var(--line-height-tight);
  color: var(--color-text-primary);
}

.register-entry__description {
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-relaxed);
}

.register-entry__action {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-brand);
  font-size: var(--font-size-callout);
  font-weight: 650;
}

.register-entry__action svg {
  transition: transform var(--duration-fast) var(--ease-spring);
}

.register-entry__choice:hover .register-entry__action svg {
  transform: translateX(4px);
}

.register-entry__hint {
  margin: var(--space-7) 0 0;
}

@media (max-width: 900px) {
  .register-entry__choices {
    grid-template-columns: 1fr;
  }

  .register-entry__choice {
    min-height: 0;
  }
}

@media (max-width: 560px) {
  .register-entry {
    padding: var(--space-5) var(--space-4) var(--space-8);
  }

  .register-entry__header {
    gap: var(--space-3);
  }

  .register-entry__title {
    font-size: var(--font-size-title-3);
  }
}

@media (prefers-reduced-motion: reduce) {
  .register-entry__choice,
  .register-entry__action svg {
    transition: none;
  }
}
</style>

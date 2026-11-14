<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const router = useRouter()
const keyword = ref('')

/** 站内没有搜索页，退化为「按关键词跳到义工列表并带上筛选」。 */
function search() {
  const value = keyword.value.trim()
  router.push(value ? { path: '/activities/mine', query: { search: value } } : '/activities/mine')
}
</script>

<template>
  <div class="yg2-page notfound-page">
    <div class="notfound-card">
      <div class="notfound-art" aria-hidden="true">
        <span class="notfound-code">404</span>
        <svg viewBox="0 0 120 60" width="150" height="76" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
          <path d="M14 46c10-16 22-24 34-24 14 0 22 10 30 10 9 0 16-7 28-18" />
          <circle cx="104" cy="16" r="4" />
        </svg>
      </div>
      <h1 class="yg2-page-header__title">{{ t('nav.pages.NotFound.title') }}</h1>
      <p class="yg2-page-header__subtitle">
        {{ t('nav.pages.NotFound.description') }}
      </p>
      <form class="notfound-search" @submit.prevent="search">
        <input
          v-model="keyword"
          class="yg2-input"
          type="search"
          placeholder="搜一下义工项目名称"
          aria-label="搜索义工项目"
        />
        <button class="yg2-btn yg2-btn--primary" type="submit">搜索</button>
      </form>
      <div class="notfound-actions">
        <button class="yg2-btn yg2-btn--secondary" type="button" @click="router.push('/')">
          {{ t('nav.pages.NotFound.action.back') }}
        </button>
        <button class="yg2-btn yg2-btn--quiet" type="button" @click="router.back()">
          返回上一页
        </button>
      </div>
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
  gap: 8px;
  padding: 40px 32px 32px;
  text-align: center;
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-26);
  box-shadow: var(--yg2-shadow-ambient);
  max-width: 440px;
  width: 100%;
}

.notfound-art {
  position: relative;
  display: grid;
  place-items: center;
  margin-bottom: 4px;
  color: var(--yg2-text-tertiary);
}

.notfound-code {
  font-size: 58px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.04em;
  color: var(--yg2-accent);
}

.notfound-art svg {
  margin-top: -6px;
}

.notfound-search {
  display: flex;
  gap: 8px;
  width: 100%;
  margin: 14px 0 6px;
}

.notfound-search .yg2-input {
  flex: 1 1 auto;
  min-width: 0;
}

.notfound-actions {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}
</style>

<script setup lang="ts">
import { ElCollapse } from 'element-plus/es/components/collapse/index.mjs'
import { ElCollapseItem } from 'element-plus/es/components/collapse/index.mjs'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import router from '@/router'
import api from '@/api'
import { onMounted, ref } from 'vue'

const { t } = useI18n()
const header = useHeaderStore()
const version = ref('—')

// 开源仓库地址与 package.json 的 repository 字段一致；许可协议直接指向仓库内的 LICENSE。
const repositoryUrl = 'https://github.com/xiaobo2010/zvp5'
const licenseUrl = `${repositoryUrl}/blob/main/LICENSE`

const platform = 'Web（PWA）'
const support = '桌面浏览器 · 平板'

onMounted(async () => {
  const value = await api.version.getVersion()
  if (value) version.value = value
})

header.setHeader(t('nav.about'))
</script>

<template>
  <div class="yg2-page about-page">
    <button class="about-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">About</p>
        <h1 class="yg2-page-header__title">{{ t('nav.about') }}</h1>
        <p class="yg2-page-header__subtitle">{{ t('about.alternative') }}</p>
      </div>
    </header>

    <section class="about-card about-board">
      <div class="about-mark">
        <img
          class="about-logo"
          :src="`/favicon.png?v=${version}`"
          alt="镇海中学义工平台 Logo"
          width="96"
          height="96"
        />
      </div>
      <div class="about-copy">
        <p class="about-kicker">Volunteer System</p>
        <h2 class="about-name">镇海中学义工平台</h2>
        <p class="about-bio">{{ t('about.bio') }}</p>
      </div>
      <dl class="yg2-kv about-specs">
        <div class="about-spec">
          <dt>版本</dt>
          <dd class="about-spec-version">{{ version }}</dd>
        </div>
        <div class="about-spec">
          <dt>平台</dt>
          <dd>{{ platform }}<span class="about-spec-sub">{{ support }}</span></dd>
        </div>
        <div class="about-spec">
          <dt>许可</dt>
          <dd>{{ t('about.license') }}</dd>
        </div>
        <div class="about-spec">
          <dt>起始</dt>
          <dd>2018</dd>
        </div>
      </dl>
    </section>

    <section class="about-card about-docs">
      <div class="about-docs-head">
        <h2 class="yg2-card__title">关于本项目</h2>
      </div>
      <p class="about-credit">
        &copy; 2018-2026 | {{ t('about.footer') }} | {{ t('about.license') }}
      </p>
      <ElCollapse class="about-collapse">
        <ElCollapseItem :title="t('about.repository.license')" name="license">
          <p class="about-p">
            {{ t('about.license') }}，完整条款见仓库内的
            <a class="about-link" :href="licenseUrl" target="_blank" rel="noopener noreferrer">
              LICENSE
            </a>
            文件。
          </p>
        </ElCollapseItem>
        <ElCollapseItem :title="t('about.privacy.title')" name="privacy">
          <p class="about-p">{{ t('about.privacy.content') }}</p>
        </ElCollapseItem>
        <ElCollapseItem title="开源仓库" name="repository">
          <p class="about-p">
            源码与版本历史托管在
            <a class="about-link" :href="repositoryUrl" target="_blank" rel="noopener noreferrer">
              github.com/xiaobo2010/zvp5
            </a>
            ，当前版本 {{ version }}。
          </p>
        </ElCollapseItem>
        <ElCollapseItem title="更新日志" name="changelog">
          <p class="about-p">
            每个版本的更新公告由系统同步为站内通知，可在
            <button
              class="about-link about-link--button"
              type="button"
              @click="router.push('/notifications')"
            >
              站内通知
            </button>
            里查看 {{ version }} 及其它版本说明。
          </p>
        </ElCollapseItem>
      </ElCollapse>
      <div class="about-actions">
        <button
          class="yg2-btn yg2-btn--secondary"
          type="button"
          data-action="changelog"
          @click="router.push('/notifications')"
        >
          查看更新日志
        </button>
      </div>
    </section>
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

.about-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.about-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.about-card {
  padding: 24px;
  background: var(--yg2-surface);
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-26);
  box-shadow: var(--yg2-shadow-ambient);
}

.about-board {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 16px 24px;
  align-items: center;
}

.about-mark {
  display: grid;
  place-items: center;
  width: 96px;
  height: 96px;
  border-radius: var(--yg2-r-20);
  background: var(--yg2-surface-sunken);
}

.about-logo {
  width: 72px;
  height: 72px;
  border-radius: var(--yg2-r-12);
}

.about-kicker {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--yg2-text-tertiary);
}

.about-name {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: var(--yg2-text);
}

.about-bio {
  margin: 6px 0 0;
  font-size: 14px;
  color: var(--yg2-text-secondary);
}

.about-specs {
  grid-column: 1 / -1;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  padding-top: 16px;
  border-top: 1px solid var(--yg2-line);
}

.about-spec {
  min-width: 0;
}

.about-spec dd {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-wrap: anywhere;
}

.about-spec-version {
  font-variant-numeric: tabular-nums;
  color: var(--yg2-accent);
}

.about-spec-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--yg2-text-tertiary);
}

.about-docs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.about-docs-head .yg2-card__title {
  margin: 0 0 8px;
}

.about-credit {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--yg2-text-tertiary);
}

.about-collapse {
  --el-collapse-border-color: var(--yg2-line);
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  --el-collapse-header-text-color: var(--yg2-text);
  --el-collapse-content-text-color: var(--yg2-text-secondary);
  border-top: 1px solid var(--yg2-line);
  border-bottom: 1px solid var(--yg2-line);
}

.about-collapse :deep(.el-collapse-item__header) {
  font-size: 14px;
  font-weight: 500;
  transition: color 180ms var(--yg2-ease-out);
}

.about-collapse :deep(.el-collapse-item__header:hover),
.about-collapse :deep(.el-collapse-item__header.is-active) {
  color: var(--yg2-accent);
}

.about-collapse :deep(.el-collapse-item__content) {
  padding: 0 0 14px;
  line-height: 1.7;
}

.about-p {
  margin: 0;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.about-link {
  color: var(--yg2-accent);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--yg2-accent) 40%, transparent);
}

.about-link:hover {
  border-bottom-color: var(--yg2-accent);
}

.about-link--button {
  padding: 0;
  border: 0;
  border-bottom: 1px solid color-mix(in srgb, var(--yg2-accent) 40%, transparent);
  background: none;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.about-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 599px) {
  .about-board {
    grid-template-columns: minmax(0, 1fr);
  }

  .about-specs {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .about-card {
    padding: 18px;
  }
}
</style>

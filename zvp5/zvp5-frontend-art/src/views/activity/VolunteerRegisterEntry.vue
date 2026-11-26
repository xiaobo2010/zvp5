<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ArrowLeft, CirclePlusFilled, EditPen, InfoFilled, Plus } from '@element-plus/icons-vue'
import { useHeaderStore } from '@/stores/header'
import { ensureRegisterAccess } from './registerAccess'

// /activity/register 的操作选择页：原登记页拆成「添加义工 / 修改义工」两个入口，
// 两者进入同一个编辑器，只是预选栏目不同；「创建义工」另外通往活动创建页。
// 带 activity 的深链会由路由守卫直接分流，不会停留在本页。

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const headerStore = useHeaderStore()

headerStore.setHeader(t('nav.registerEntry.title'))

// 冷启动直接打开本页时，浏览器历史里没有应用内上一页；此时 router.back()
// 可能把用户带出应用。只有上一条确实是应用内路由时才原路返回（连同上一页
// 自己的 query），否则显式回退到活动列表。
const FALLBACK_ROUTE = '/activities/mine'

function hasInAppHistory(): boolean {
  const back = router.options.history.state.back
  return typeof back === 'string' && back.startsWith('/')
}

function goBack() {
  if (hasInAppHistory()) {
    router.back()
    return
  }
  // 回退保留当前 query：活动列表会读取 page / perpage / search 等参数。
  void router.replace({ path: FALLBACK_ROUTE, query: route.query })
}

// 进入编辑器时保留 query：activity/session 等参数不能在换页时丢掉。
function target(mode: 'create' | 'edit') {
  return { name: 'activity-register-mode', params: { mode }, query: route.query }
}

onMounted(async () => {
  // 未登录 / 无 admin·department 权限时由 helper 负责跳转，本页不再重复判断。
  await ensureRegisterAccess(router)
})
</script>

<template>
  <div class="yg-register-entry yg-container">
    <header class="yg-page-header yg-register-entry__header">
      <div>
        <h1 class="yg-page-header__title">{{ t('nav.registerEntry.title') }}</h1>
        <p class="yg-page-header__subtitle">{{ t('nav.registerEntry.subtitle') }}</p>
      </div>
      <div class="yg-page-header__actions">
        <button type="button" class="yg-btn yg-btn--ghost" @click="goBack">
          <ElIcon class="yg-btn__icon"><ArrowLeft /></ElIcon>
          <span>{{ t('nav.registerEntry.back') }}</span>
        </button>
      </div>
    </header>

    <div class="yg-register-entry__choices">
      <RouterLink
        class="yg-register-entry__choice yg-register-entry__choice--create"
        :to="target('create')"
      >
        <span class="yg-register-entry__choice-number" aria-hidden="true">01</span>
        <span class="yg-register-entry__choice-bracket" aria-hidden="true"></span>
        <span class="yg-register-entry__choice-icon" aria-hidden="true">
          <ElIcon><Plus /></ElIcon>
        </span>
        <span class="yg-register-entry__choice-body">
          <span class="yg-register-entry__choice-title">
            {{ t('nav.registerEntry.create.title') }}
          </span>
          <span class="yg-register-entry__choice-desc">
            {{ t('nav.registerEntry.create.desc') }}
          </span>
        </span>
        <span class="yg-register-entry__choice-action">
          {{ t('nav.registerEntry.create.action') }}
        </span>
      </RouterLink>

      <RouterLink
        class="yg-register-entry__choice yg-register-entry__choice--edit"
        :to="target('edit')"
      >
        <span class="yg-register-entry__choice-number" aria-hidden="true">02</span>
        <span class="yg-register-entry__choice-bracket" aria-hidden="true"></span>
        <span class="yg-register-entry__choice-icon" aria-hidden="true">
          <ElIcon><EditPen /></ElIcon>
        </span>
        <span class="yg-register-entry__choice-body">
          <span class="yg-register-entry__choice-title">
            {{ t('nav.registerEntry.edit.title') }}
          </span>
          <span class="yg-register-entry__choice-desc">
            {{ t('nav.registerEntry.edit.desc') }}
          </span>
        </span>
        <span class="yg-register-entry__choice-action">
          {{ t('nav.registerEntry.edit.action') }}
        </span>
      </RouterLink>

      <RouterLink
        class="yg-register-entry__choice yg-register-entry__choice--create-activity"
        :to="{ name: 'activity-create-home' }"
      >
        <span class="yg-register-entry__choice-number" aria-hidden="true">03</span>
        <span class="yg-register-entry__choice-bracket" aria-hidden="true"></span>
        <span class="yg-register-entry__choice-icon" aria-hidden="true">
          <ElIcon><CirclePlusFilled /></ElIcon>
        </span>
        <span class="yg-register-entry__choice-body">
          <span class="yg-register-entry__choice-title">
            {{ t('nav.registerEntry.createActivity.title') }}
          </span>
          <span class="yg-register-entry__choice-desc">
            {{ t('nav.registerEntry.createActivity.desc') }}
          </span>
        </span>
        <span class="yg-register-entry__choice-action">
          {{ t('nav.registerEntry.createActivity.action') }}
        </span>
      </RouterLink>
    </div>

    <p class="yg-register-entry__hint">
      <ElIcon class="yg-register-entry__hint-icon" aria-hidden="true"><InfoFilled /></ElIcon>
      <span>{{ t('nav.registerEntry.hint') }}</span>
    </p>
  </div>
</template>

<style scoped>
/* 操作选择页：瑞士式留白 + 苹果式大圆角卡片，颜色/间距/动效全部走语义令牌。 */
.yg-register-entry {
  padding-block: var(--yg-space-6) var(--yg-space-8);
}

.yg-register-entry__header {
  margin-block-end: var(--yg-space-6);
}

.yg-register-entry__choices {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--yg-space-5);
}

/* 卡片整体是一个 44px 以上的可点击区域，整卡可聚焦、可回车进入。
   Phi 皮肤会补切角、硬阴影和 2px 上浮；这里只保留基础布局与降级态。 */
.yg-register-entry__choice {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  min-height: var(--yg-space-9);
  padding: var(--yg-space-6);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-2xl);
  background: var(--yg-bg-surface);
  box-shadow: var(--yg-shadow-xs);
  color: inherit;
  text-decoration: none;
  transition:
    box-shadow var(--yg-dur-base) var(--yg-ease-out),
    border-color var(--yg-dur-base) var(--yg-ease-out),
    background-color var(--yg-dur-base) var(--yg-ease-out);
}

/* 编号：右上角显示数字，避开左上角图标 */
.yg-register-entry__choice-number {
  position: absolute;
  top: var(--yg-space-3);
  right: var(--yg-space-4);
  color: var(--yg-color-primary);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1;
  opacity: 0.5;
  pointer-events: none;
}

/* 角括号：右下角装饰 */
.yg-register-entry__choice-bracket {
  position: absolute;
  right: var(--yg-space-4);
  bottom: var(--yg-space-4);
  width: 18px;
  height: 18px;
  border-right: 2px solid var(--yg-color-primary);
  border-bottom: 2px solid var(--yg-color-primary);
  opacity: 0.3;
  pointer-events: none;
  transition: opacity var(--yg-dur-base) var(--yg-ease-out);
}

.yg-register-entry__choice:hover .yg-register-entry__choice-bracket {
  opacity: 0.6;
}

.yg-register-entry__choice:hover {
  border-color: var(--yg-border-strong);
  box-shadow: var(--yg-shadow-md);
}

.yg-register-entry__choice:active {
  background: var(--yg-bg-surface-sunken);
  box-shadow: var(--yg-shadow-sm);
}

.yg-register-entry__choice:focus-visible {
  outline: none;
  border-color: var(--yg-color-primary);
  box-shadow: var(--yg-focus-ring), var(--yg-shadow-sm);
}

/* 入口卡片本身总是可用；保留禁用态样式，供后续按权限收紧单个操作时直接复用。 */
.yg-register-entry__choice.is-disabled,
.yg-register-entry__choice[aria-disabled='true'] {
  border-color: var(--yg-border-subtle);
  background: var(--yg-bg-surface-sunken);
  box-shadow: none;
  cursor: not-allowed;
}

.yg-register-entry__choice.is-disabled .yg-register-entry__choice-title,
.yg-register-entry__choice.is-disabled .yg-register-entry__choice-desc,
.yg-register-entry__choice.is-disabled .yg-register-entry__choice-action,
.yg-register-entry__choice[aria-disabled='true'] .yg-register-entry__choice-title,
.yg-register-entry__choice[aria-disabled='true'] .yg-register-entry__choice-desc,
.yg-register-entry__choice[aria-disabled='true'] .yg-register-entry__choice-action {
  color: var(--yg-text-disabled-control);
}

.yg-register-entry__choice-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--yg-space-9);
  height: var(--yg-space-9);
  border-radius: var(--yg-radius-lg);
  background: var(--yg-color-primary-soft);
  color: var(--yg-text-link);
  font-size: var(--yg-font-size-3xl);
}

/* 修改入口用中性底：三个操作都安全，只有主色留给「添加义工」。
   创建义工同样是中性底，但图标保留主色，和纯中性的「修改义工」区分开。 */
.yg-register-entry__choice--edit .yg-register-entry__choice-icon {
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-secondary);
}

.yg-register-entry__choice--create-activity .yg-register-entry__choice-icon {
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-color-primary);
}

.yg-register-entry__choice-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--yg-space-2);
}

.yg-register-entry__choice-title {
  font-size: var(--yg-font-size-2xl);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
}

.yg-register-entry__choice-desc {
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-secondary);
}

.yg-register-entry__choice-action {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  align-self: flex-start;
  min-height: 44px;
  padding: var(--yg-space-2) var(--yg-space-4);
  font-size: var(--yg-font-size-md);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-link);
  border: 1px solid var(--yg-border-default);
  border-radius: 0;
  transition:
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-register-entry__choice:hover .yg-register-entry__choice-action {
  background-color: var(--yg-bg-surface-hover);
  border-color: var(--yg-color-primary);
}

.yg-register-entry__choice-action::after {
  content: '→';
  transition: transform var(--yg-dur-fast) var(--yg-ease-out);
}

.yg-register-entry__choice:hover .yg-register-entry__choice-action::after {
  transform: translateX(var(--yg-space-1));
}

.yg-register-entry__choice:active .yg-register-entry__choice-action {
  transform: scale(0.98);
}

.yg-register-entry__hint {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-block: var(--yg-space-6) 0;
  padding: var(--yg-space-3) var(--yg-space-4);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-lg);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.yg-register-entry__hint-icon {
  flex: none;
  color: var(--yg-text-link);
  font-size: var(--yg-font-size-lg);
}

/* 三张卡片在平板竖屏/窄窗口下一列排开：并排会把描述挤成多行，失去留白节奏。 */
@media (max-width: 1024px) {
  .yg-register-entry__choices {
    grid-template-columns: 1fr;
    gap: var(--yg-space-4);
  }
}

@media (max-width: 640px) {
  .yg-register-entry__choice {
    padding: var(--yg-space-5);
  }

  .yg-register-entry__choice-action {
    align-self: stretch;
    justify-content: center;
  }

  /* 移动端 CTA 通栏，右下角括号会压到按钮，直接隐藏 */
  .yg-register-entry__choice-bracket {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .yg-register-entry__choice,
  .yg-register-entry__choice-action,
  .yg-register-entry__choice-action::after,
  .yg-register-entry__choice-bracket {
    transition: none;
  }

  .yg-register-entry__choice:hover .yg-register-entry__choice-action::after {
    transform: none;
  }
}

@supports (corner-shape: squircle) {
  .yg-register-entry__choice,
  .yg-register-entry__choice-icon,
  .yg-register-entry__hint {
    corner-shape: squircle;
  }
}
</style>

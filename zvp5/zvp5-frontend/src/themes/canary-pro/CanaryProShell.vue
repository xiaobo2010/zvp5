<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useWindowSize } from '@vueuse/core'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ArrowLeft, ArrowRight, SwitchButton, Lock, Moon, Sunny } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { modifyPasswordDialogs } from '@/views/user/modifyPasswordDialogs'
import { PRO_NAV_GROUPS, type ProNavItem } from './nav'

defineProps<{ dark?: boolean }>()
const emit = defineEmits<{ 'toggle-dark': [] }>()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const headerStore = useHeaderStore()

const { width } = useWindowSize()

// 桌面端保留一条可收起的固定导航栏；≤900px 变成覆盖式抽屉。
const rail = ref(false)
const drawerOpen = ref(false)

watch(
  () => width.value,
  (w) => {
    rail.value = w > 900 && w <= 1280
    if (w <= 900) {
      drawerOpen.value = false
    }
  },
  { immediate: true }
)

const groups = computed(() =>
  PRO_NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.show(userStore.position))
  })).filter((group) => group.items.length > 0)
)

const activeGroup = computed(() => {
  const group = groups.value.find((g) => g.items.some((item) => item.judge(route.path)))
  return group?.label ?? '工作台'
})

const pageTitle = computed(() => {
  const full = headerStore.header
  const suffix = ` - ${headerStore.base}`
  return full.endsWith(suffix) ? full.slice(0, -suffix.length) : full
})

const today = computed(() => {
  const now = new Date()
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${now.getMonth() + 1}月${now.getDate()}日 · ${weekdays[now.getDay()]}`
})

const roleLabel = computed(() => {
  const roles = userStore.position
  if (roles.includes('admin')) return '管理员'
  if (roles.includes('department')) return '部门'
  if (roles.includes('secretary')) return '团支书'
  if (roles.includes('auditor')) return '审计'
  return '志愿者'
})

const avatarInitial = computed(() => userStore.name.trim().slice(0, 1) || 'Z')

function go(item: ProNavItem) {
  drawerOpen.value = false
  if (item.path !== route.path) {
    void router.push(item.path)
  }
}

async function changePassword() {
  try {
    await modifyPasswordDialogs(String(userStore.id), 'zh-CN', userStore.resetPassword)
  } catch {
    // 取消改密不算异常
  }
}

async function logout() {
  try {
    await ElMessageBox.confirm('退出登录后需要重新验证身份，确定要退出吗？', '退出登录', {
      confirmButtonText: '退出登录',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonClass: 'el-button--danger',
      closeOnClickModal: false
    })
    await userStore.removeUser()
    await router.replace('/user/login')
  } catch {
    // 用户取消
  }
}

let previousOverflow = ''
watch(drawerOpen, (open) => {
  if (open && width.value <= 900) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  } else if (!open) {
    document.body.style.overflow = previousOverflow
  }
})

onUnmounted(() => {
  document.body.style.overflow = previousOverflow
})
</script>

<template>
  <div
    class="pro-app"
    :class="{ 'is-rail': rail, 'is-plain': !userStore.isLogin, 'is-drawer-open': drawerOpen }"
  >
    <aside v-if="userStore.isLogin" class="pro-rail" aria-label="工作台导航">
      <div class="pro-rail__brand">
        <span class="pro-rail__mark" aria-hidden="true">Z</span>
        <span class="pro-rail__brand-text">
          <strong>ZVMS</strong>
          <small>Workspace</small>
        </span>
        <button
          type="button"
          class="pro-rail__collapse"
          :aria-label="rail ? '展开导航' : '收起导航'"
          :title="rail ? '展开导航' : '收起导航'"
          @click="rail = !rail"
        >
          <ArrowRight v-if="rail" width="14" height="14" />
          <ArrowLeft v-else width="14" height="14" />
        </button>
      </div>

      <div class="pro-rail__workspace">
        <span class="pro-rail__workspace-dot" aria-hidden="true" />
        <span class="pro-rail__workspace-text">
          <small>当前工作区</small>
          <strong>{{ activeGroup }}</strong>
        </span>
      </div>

      <nav class="pro-rail__nav">
        <section v-for="group in groups" :key="group.key" class="pro-group">
          <h2 class="pro-group__label">{{ group.label }}</h2>
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            class="pro-nav-item"
            :class="{ 'is-active': item.judge(route.path) }"
            :title="`${item.label} · ${item.caption}`"
            @click="go(item)"
          >
            <span class="pro-nav-item__icon"><component :is="item.icon" /></span>
            <span class="pro-nav-item__text">
              <strong>{{ item.label }}</strong>
              <small>{{ item.caption }}</small>
            </span>
          </button>
        </section>
      </nav>

      <div class="pro-rail__foot">
        <div class="pro-identity">
          <span class="pro-identity__avatar">{{ avatarInitial }}</span>
          <span class="pro-identity__text">
            <strong>{{ userStore.name || '未登录' }}</strong>
            <small>{{ roleLabel }}</small>
          </span>
        </div>
        <div class="pro-rail__foot-actions">
          <button type="button" class="pro-rail__foot-btn" title="密码修改" @click="changePassword">
            <Lock width="15" height="15" />
          </button>
          <button type="button" class="pro-rail__foot-btn" title="退出登录" @click="logout">
            <SwitchButton width="15" height="15" />
          </button>
        </div>
      </div>
    </aside>

    <div class="pro-main">
      <header class="pro-topbar">
        <button
          v-if="userStore.isLogin"
          type="button"
          class="pro-topbar__menu"
          aria-label="打开导航"
          @click="drawerOpen = true"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <div class="pro-topbar__heading">
          <span class="pro-topbar__crumb">
            <span>Workspace</span>
            <span class="pro-topbar__sep">/</span>
            <span>{{ activeGroup }}</span>
          </span>
          <h1 class="pro-topbar__title">{{ pageTitle }}</h1>
        </div>
        <div class="pro-topbar__actions">
          <span class="pro-topbar__date">{{ today }}</span>
          <button
            type="button"
            class="pro-icon-btn"
            :title="dark ? '切换到浅色模式' : '切换到深色模式'"
            :aria-label="dark ? '切换到浅色模式' : '切换到深色模式'"
            @click="emit('toggle-dark')"
          >
            <Sunny v-if="dark" width="16" height="16" />
            <Moon v-else width="16" height="16" />
          </button>
        </div>
      </header>

      <main class="pro-workspace">
        <div class="pro-workspace__inner">
          <slot />
        </div>
      </main>
    </div>

    <div v-if="drawerOpen" class="pro-drawer-mask" @click="drawerOpen = false" />
  </div>
</template>

<style scoped>
.pro-app {
  display: flex;
  width: 100%;
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  color: var(--color-text-primary);
  font-family: var(--font-system);
  background: var(--pro-canvas, var(--color-bg-canvas));
}

.pro-app.is-plain {
  display: block;
  height: auto;
  min-height: 100%;
  overflow: visible;
}

/* ── 固定导航栏（工作台左侧） ───────────────────────────── */
.pro-rail {
  display: flex;
  flex: 0 0 var(--pro-rail-w, 248px);
  flex-direction: column;
  gap: var(--space-4);
  width: var(--pro-rail-w, 248px);
  height: 100%;
  padding: var(--space-5) var(--space-4);
  border-right: 1px solid var(--pro-hairline, rgba(255, 255, 255, 0.08));
  background: var(--pro-rail-fill, rgba(12, 15, 22, 0.86));
  -webkit-backdrop-filter: blur(22px) saturate(140%);
  backdrop-filter: blur(22px) saturate(140%);
  transition: flex-basis 320ms var(--ease-smooth), width 320ms var(--ease-smooth);
}

.pro-rail__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 var(--space-2);
}

.pro-rail__mark {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: linear-gradient(145deg, #2f7cff, #7b5cff);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  box-shadow: 0 8px 20px -8px rgba(80, 120, 255, 0.8);
}

.pro-rail__brand-text {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  line-height: 1.15;
}

.pro-rail__brand-text strong {
  font-size: 14px;
  font-weight: 650;
  letter-spacing: 0.04em;
}

.pro-rail__brand-text small {
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.pro-rail__collapse {
  display: grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: var(--color-text-tertiary);
}

.pro-rail__collapse:hover {
  background: var(--pro-hover, rgba(255, 255, 255, 0.06));
  color: var(--color-text-primary);
}

.pro-rail__workspace {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--pro-hairline, rgba(255, 255, 255, 0.08));
  border-radius: var(--radius-md);
  background: var(--pro-card-fill, rgba(255, 255, 255, 0.04));
}

.pro-rail__workspace-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #30d158;
  box-shadow: 0 0 0 4px rgba(48, 209, 88, 0.14);
}

.pro-rail__workspace-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.2;
}

.pro-rail__workspace-text small {
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.pro-rail__workspace-text strong {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pro-rail__nav {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  gap: var(--space-5);
  overflow-y: auto;
  overflow-x: hidden;
}

.pro-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pro-group__label {
  margin: 0 0 var(--space-2);
  padding: 0 var(--space-3);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.pro-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 42px;
  padding: 6px var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  text-align: left;
  transition: background 180ms var(--ease-smooth), color 180ms var(--ease-smooth);
}

.pro-nav-item:hover {
  background: var(--pro-hover, rgba(255, 255, 255, 0.06));
  color: var(--color-text-primary);
}

.pro-nav-item.is-active {
  background: var(--pro-active, rgba(47, 124, 255, 0.16));
  color: var(--color-text-primary);
  box-shadow: inset 0 0 0 1px rgba(96, 148, 255, 0.22);
}

.pro-nav-item__icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 18px;
  height: 18px;
  opacity: 0.9;
}

.pro-nav-item__icon :deep(svg) {
  width: 17px;
  height: 17px;
}

.pro-nav-item__text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.2;
}

.pro-nav-item__text strong {
  overflow: hidden;
  font-size: 13px;
  font-weight: 550;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pro-nav-item__text small {
  font-size: 10px;
  letter-spacing: 0.06em;
  color: var(--color-text-tertiary);
}

.pro-rail__foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-4);
  border-top: 1px solid var(--pro-hairline, rgba(255, 255, 255, 0.08));
}

.pro-identity {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.pro-identity__avatar {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(145deg, rgba(47, 124, 255, 0.4), rgba(123, 92, 255, 0.32));
  color: #fff;
  font-size: 13px;
  font-weight: 600;
}

.pro-identity__text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.2;
}

.pro-identity__text strong {
  overflow: hidden;
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pro-identity__text small {
  font-size: 10px;
  color: var(--color-text-tertiary);
}

.pro-rail__foot-actions {
  display: flex;
  gap: 4px;
}

.pro-rail__foot-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  color: var(--color-text-tertiary);
}

.pro-rail__foot-btn:hover {
  background: var(--pro-hover, rgba(255, 255, 255, 0.06));
  color: var(--color-text-primary);
}

/* 收起态：只留下图标与首字母 */
.pro-app.is-rail .pro-rail {
  flex-basis: var(--pro-rail-rail, 88px);
  width: var(--pro-rail-rail, 88px);
  align-items: center;
  padding-inline: var(--space-3);
}

.pro-app.is-rail .pro-rail__brand-text,
.pro-app.is-rail .pro-rail__workspace-text,
.pro-app.is-rail .pro-group__label,
.pro-app.is-rail .pro-nav-item__text,
.pro-app.is-rail .pro-identity__text,
.pro-app.is-rail .pro-rail__foot-actions {
  display: none;
}

.pro-app.is-rail .pro-rail__brand,
.pro-app.is-rail .pro-rail__workspace,
.pro-app.is-rail .pro-nav-item {
  justify-content: center;
  padding-inline: 0;
}

.pro-app.is-rail .pro-rail__workspace {
  width: 44px;
  height: 44px;
}

.pro-app.is-rail .pro-nav-item {
  width: 44px;
  height: 44px;
  min-height: 44px;
}

.pro-app.is-rail .pro-rail__foot {
  justify-content: center;
}

/* ── 主区与顶栏 ─────────────────────────────────────────── */
.pro-main {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
}

.pro-topbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: 72px;
  padding: 0 clamp(20px, 3vw, 40px);
  border-bottom: 1px solid var(--pro-hairline, rgba(255, 255, 255, 0.08));
  background: var(--pro-topbar-fill, rgba(14, 17, 24, 0.72));
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  backdrop-filter: blur(20px) saturate(150%);
}

.pro-topbar__menu {
  display: none;
  place-items: center;
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  color: var(--color-text-secondary);
}

.pro-topbar__heading {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.pro-topbar__crumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.pro-topbar__sep {
  opacity: 0.5;
}

.pro-topbar__title {
  overflow: hidden;
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--color-text-primary);
}

.pro-topbar__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-3);
}

.pro-topbar__date {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.pro-icon-btn {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--pro-hairline, rgba(255, 255, 255, 0.08));
  border-radius: 50%;
  color: var(--color-text-secondary);
}

.pro-icon-btn:hover {
  background: var(--pro-hover, rgba(255, 255, 255, 0.06));
  color: var(--color-text-primary);
}

.pro-workspace {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  background:
    radial-gradient(1100px 620px at 12% -10%, rgba(47, 124, 255, 0.16), transparent 62%),
    radial-gradient(900px 560px at 96% 8%, rgba(123, 92, 255, 0.12), transparent 66%),
    var(--pro-canvas, #0a0c12);
}

.pro-workspace__inner {
  width: 100%;
  max-width: 1180px;
  min-height: 100%;
  margin: 0 auto;
  padding: clamp(20px, 3vw, 40px) clamp(20px, 3vw, 40px) 80px;
}

.pro-drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(0, 0, 0, 0.55);
}

@media (max-width: 900px) {
  .pro-rail,
  .pro-app.is-rail .pro-rail {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 70;
    width: 268px;
    flex-basis: 268px;
    align-items: stretch;
    padding: var(--space-5) var(--space-4);
    transform: translateX(-104%);
    transition: transform 300ms var(--ease-smooth);
  }

  .pro-app.is-drawer-open .pro-rail {
    transform: translateX(0);
  }

  .pro-app.is-rail .pro-rail__brand-text,
  .pro-app.is-rail .pro-rail__workspace-text,
  .pro-app.is-rail .pro-group__label,
  .pro-app.is-rail .pro-nav-item__text,
  .pro-app.is-rail .pro-identity__text {
    display: flex;
  }

  .pro-app.is-rail .pro-rail__brand,
  .pro-app.is-rail .pro-rail__workspace,
  .pro-app.is-rail .pro-nav-item {
    justify-content: flex-start;
    padding-inline: var(--space-3);
  }

  .pro-app.is-rail .pro-nav-item {
    width: 100%;
  }

  .pro-topbar__menu {
    display: grid;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pro-rail,
  .pro-nav-item,
  .pro-icon-btn {
    transition: none;
  }
}
</style>

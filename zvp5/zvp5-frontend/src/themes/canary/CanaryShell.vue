<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user'
import UserNav from '@/views/user-v2/UserNav.vue'
import ZHexGrid from '@/components/decor/ZHexGrid.vue'
import { startPhiEdgePhase, stopPhiEdgePhase } from '@/utils/phiEdgePhase'
import { isPhiPath } from '@/themes/phiRoutes'

// 外壳通过统一协议接收明暗状态，canary 自身不消费（UserNav 自己管理），
// 但保留 prop 让三种外壳在 App 里可以共用同一处渲染。
defineProps<{ dark?: boolean }>()
defineEmits<{ 'toggle-dark': [] }>()

const route = useRoute()
const userStore = useUserStore()

const { width, height } = useWindowSize()

// 竖屏（窄高比）时 UserNav 自带底部悬浮药丸导航，App 外壳只需给底部 dock 让位；
// 横屏时 UserNav 走左侧玻璃栏。
const verticalMode = computed(() => width.value < height.value * 1.2)

// Phi 主题：整个应用（普通用户端 + 后台三个管理区）统一挂 .yg-phi 类
const isPhiRoute = computed(() => isPhiPath(route.path))

// 服务端/管理台已在各自外壳内挂蜂窝底纹；其余 Phi 页面与独立通知页由外壳统一补一层。
// 登录页有自己的缓存背景，不重复叠。
const hasLocalHexGrid = computed(() => {
  const path = route.path
  return (
    path === '/server' ||
    path.startsWith('/server/') ||
    path === '/manage' ||
    path.startsWith('/manage/')
  )
})

const showPageHex = computed(
  () => (isPhiRoute.value || route.path === '/notifications') && !hasLocalHexGrid.value
)

// body 级弹层令牌由 App 统一维护（登录页也有 Phi 弹层），这里只负责外壳内部的
// 蜂窝/速度线与流光相位观察器。
onMounted(startPhiEdgePhase)

onUnmounted(stopPhiEdgePhase)
</script>

<template>
  <div :class="['yg-layout', 'yg-layout--app', { 'yg-phi': isPhiRoute }]">
    <UserNav v-if="userStore.isLogin" />
    <div class="yg-layout__main">
      <main :class="['yg-layout__content', { 'is-portrait': verticalMode && userStore.isLogin }]">
        <!-- 全局蜂窝底纹：服务端/管理台已有自己的底纹，其余页面统一补上 -->
        <div v-if="showPageHex" class="yg-page-hex" aria-hidden="true">
          <ZHexGrid
            :size="64"
            color="var(--phi-breathe, #1de9b6)"
            :opacity="0.07"
            drift
            sweep
          />
        </div>
        <!-- 速度线挂在滚动内容里，随页面上下滚动；不再 fixed 浮在视口上。 -->
        <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
        <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
        <div v-if="isPhiRoute" class="yg-phi-velocity" aria-hidden="true"></div>
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
/* 全局蜂窝底纹：固定在视口上，负 z-index 落在内容区底色之上、卡片之下。
   内容区自身有 isolation + overflow，这里必须 fixed 才不会被滚动内容带跑。 */
.yg-page-hex {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
  contain: layout paint style;
}

/* 竖屏底部 dock（UserNav 悬浮药丸）让位：药丸 56px(--yg-header-height 令牌同值)
   + 离底 12px(--yg-space-3) + 手势/刘海安全区 + 内容自身 16px 留白(--yg-space-4)。
   抽成 --yg-content-bottom-inset 是为了让页面（如服务器页）算自身高度时引用同一份
   数值，不必各自硬编码 96px；横屏不挂 is-portrait，padding 仍是 base.css 的 24px。 */
.yg-layout__content {
  --yg-content-bottom-inset: 0px;
}

.yg-layout__content.is-portrait {
  --yg-content-bottom-inset: calc(
    var(--yg-header-height) + var(--yg-space-3) + env(safe-area-inset-bottom, 0px) +
      var(--yg-space-4)
  );
  padding-bottom: var(--yg-content-bottom-inset);
}
</style>

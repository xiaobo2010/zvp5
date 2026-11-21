<script setup lang="ts">
import AppRail from './AppRail.vue'
import AppTopbar from './AppTopbar.vue'
import AppDock from './AppDock.vue'
</script>

<template>
  <div class="shell">
    <AppRail />
    <div class="column">
      <AppTopbar />
      <main id="main" class="content">
        <RouterView v-slot="{ Component, route }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="route.path" />
          </Transition>
        </RouterView>
      </main>
      <footer class="foot">
        <span>ZVP5 · Canary</span>
        <span class="dot" aria-hidden="true">·</span>
        <span>Zhenhai Volunteer Platform</span>
      </footer>
    </div>
    <AppDock />
  </div>
</template>

<style scoped>
.shell {
  min-height: 100dvh;
}

.column {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  margin-left: var(--rail-width);
}

.content {
  flex: 1;
  width: 100%;
  max-width: var(--content-max);
  margin-inline: auto;
  padding: var(--space-8) var(--gutter) var(--space-16);
}

.foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: var(--space-6) var(--gutter) var(--space-10);
  font-size: var(--text-2xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.dot {
  opacity: 0.5;
}

@media (max-width: 1080px) {
  .column {
    margin-left: 0;
  }

  .content {
    padding-top: var(--space-6);
    padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px));
  }

  .foot {
    display: none;
  }
}
</style>

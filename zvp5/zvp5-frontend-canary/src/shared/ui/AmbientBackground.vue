<script setup lang="ts">
import { computed, ref } from 'vue'
import { ambientSpecs } from '@/shared/lib/ambientLight'
import { useAmbientLight } from '@/shared/composables/useAmbientLight'

/**
 * 全局环境光层：固定定位、不参与布局、不吃指针事件。
 * 光团的颜色与透明度过 CSS 主题变量控制，位置与呼吸由 AmbientLightEngine 逐帧写入。
 */
const host = ref<HTMLElement | null>(null)
const { compact } = useAmbientLight(host)
const blobs = computed(() => ambientSpecs(compact.value))
</script>

<template>
  <div ref="host" class="ambient" aria-hidden="true">
    <span
      v-for="blob in blobs"
      :key="blob.id"
      class="ambient-blob"
      :class="`blob-${blob.id}`"
    />
  </div>
</template>

<style scoped>
.ambient {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
  contain: strict;
}

.ambient-blob {
  position: absolute;
  top: 0;
  left: 0;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  opacity: 0;
  transform: translate3d(-200vw, -200vh, 0);
  will-change: transform, opacity;
  background:
    radial-gradient(50% 50% at 38% 34%, var(--b1, transparent), transparent 72%),
    radial-gradient(48% 46% at 66% 66%, var(--b2, transparent), transparent 74%);
}

/* --- 浅色：奶油白底上的香槟金 / 珍珠白 / 琥珀 --------------------------- */
.blob-gold {
  --b1: rgba(205, 155, 72, 0.3);
  --b2: rgba(238, 196, 124, 0.2);
}

.blob-pearl {
  --b1: rgba(255, 253, 246, 0.62);
  --b2: rgba(255, 248, 234, 0.34);
}

.blob-amber {
  --b1: rgba(226, 168, 92, 0.26);
  --b2: rgba(210, 148, 70, 0.16);
}

.blob-blue {
  --b1: rgba(158, 186, 206, 0.2);
  --b2: rgba(196, 214, 226, 0.13);
}

.blob-lavender {
  --b1: rgba(186, 176, 204, 0.17);
  --b2: rgba(206, 198, 220, 0.11);
}

.blob-ivory {
  --b1: rgba(252, 244, 226, 0.52);
  --b2: rgba(255, 251, 242, 0.28);
}

/* --- 深色：降低明度，避免变成霓虹 --------------------------------------- */
[data-theme='dark'] .blob-gold {
  --b1: rgba(226, 178, 96, 0.2);
  --b2: rgba(206, 152, 74, 0.12);
}

[data-theme='dark'] .blob-pearl {
  --b1: rgba(255, 246, 228, 0.08);
  --b2: rgba(240, 228, 205, 0.04);
}

[data-theme='dark'] .blob-amber {
  --b1: rgba(212, 150, 72, 0.17);
  --b2: rgba(186, 122, 56, 0.1);
}

[data-theme='dark'] .blob-blue {
  --b1: rgba(122, 158, 188, 0.14);
  --b2: rgba(96, 128, 156, 0.08);
}

[data-theme='dark'] .blob-lavender {
  --b1: rgba(148, 136, 176, 0.12);
  --b2: rgba(120, 110, 148, 0.07);
}

[data-theme='dark'] .blob-ivory {
  --b1: rgba(240, 228, 205, 0.06);
  --b2: rgba(226, 214, 192, 0.03);
}

@media (prefers-reduced-motion: reduce) {
  .ambient-blob {
    will-change: auto;
  }
}
</style>

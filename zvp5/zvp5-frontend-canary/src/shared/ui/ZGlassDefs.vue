<script setup lang="ts">
import { computed } from 'vue'
import {
  GLASS_MATERIALS,
  glassDisplacementMap,
  glassFilterId,
  supportsGlassRefraction,
  type GlassMaterial
} from '@/shared/lib/glass'

/**
 * 全站只挂一份玻璃滤镜定义（App.vue 中渲染一次）。
 * 每个滤镜 = 位移图 + 三通道色散 + 边缘遮罩，节点是静态的，
 * 不会随页面交互重排；材质差异只体现在位移量上。
 */
const materials = Object.keys(GLASS_MATERIALS) as GlassMaterial[]

const map = computed(() => (supportsGlassRefraction() ? glassDisplacementMap() : ''))
const enabled = computed(() => Boolean(map.value))
</script>

<template>
  <svg v-if="enabled" class="glass-defs" aria-hidden="true" focusable="false">
    <defs>
      <template v-for="material in materials" :key="material">
        <radialGradient :id="`${glassFilterId(material)}-edge`" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="black" stop-opacity="0" />
          <stop offset="72%" stop-color="black" stop-opacity="0" />
          <stop offset="100%" stop-color="white" stop-opacity="1" />
        </radialGradient>
        <filter
          :id="glassFilterId(material)"
          x="-30%"
          y="-30%"
          width="160%"
          height="160%"
          colorInterpolationFilters="sRGB"
        >
          <feImage
            x="0"
            y="0"
            width="100%"
            height="100%"
            preserveAspectRatio="none"
            result="MAP"
            :href="map"
          />
          <feColorMatrix
            in="MAP"
            type="matrix"
            values="0.3 0.3 0.3 0 0
                    0.3 0.3 0.3 0 0
                    0.3 0.3 0.3 0 0
                    0 0 0 1 0"
            result="EDGE_INTENSITY"
          />
          <feComponentTransfer in="EDGE_INTENSITY" result="EDGE_MASK">
            <feFuncA
              type="discrete"
              :tableValues="`0 ${GLASS_MATERIALS[material].aberration * 0.05} 1`"
            />
          </feComponentTransfer>
          <feOffset in="SourceGraphic" dx="0" dy="0" result="CENTER_ORIGINAL" />
          <feDisplacementMap
            in="SourceGraphic"
            in2="MAP"
            :scale="-GLASS_MATERIALS[material].scale"
            xChannelSelector="R"
            yChannelSelector="B"
            result="RED_DISPLACED"
          />
          <feColorMatrix
            in="RED_DISPLACED"
            type="matrix"
            values="1 0 0 0 0
                    0 0 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="RED_CHANNEL"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="MAP"
            :scale="
              -GLASS_MATERIALS[material].scale +
              GLASS_MATERIALS[material].scale * GLASS_MATERIALS[material].aberration * 0.05
            "
            xChannelSelector="R"
            yChannelSelector="B"
            result="GREEN_DISPLACED"
          />
          <feColorMatrix
            in="GREEN_DISPLACED"
            type="matrix"
            values="0 0 0 0 0
                    0 1 0 0 0
                    0 0 0 0 0
                    0 0 0 1 0"
            result="GREEN_CHANNEL"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="MAP"
            :scale="
              -GLASS_MATERIALS[material].scale +
              GLASS_MATERIALS[material].scale * GLASS_MATERIALS[material].aberration * 0.1
            "
            xChannelSelector="R"
            yChannelSelector="B"
            result="BLUE_DISPLACED"
          />
          <feColorMatrix
            in="BLUE_DISPLACED"
            type="matrix"
            values="0 0 0 0 0
                    0 0 0 0 0
                    0 0 1 0 0
                    0 0 0 1 0"
            result="BLUE_CHANNEL"
          />
          <feBlend in="GREEN_CHANNEL" in2="BLUE_CHANNEL" mode="screen" result="GB_COMBINED" />
          <feBlend in="RED_CHANNEL" in2="GB_COMBINED" mode="screen" result="RGB_COMBINED" />
          <feGaussianBlur
            in="RGB_COMBINED"
            :stdDeviation="Math.max(0.1, 0.5 - GLASS_MATERIALS[material].aberration * 0.1)"
            result="ABERRATED_BLURRED"
          />
          <feComposite
            in="ABERRATED_BLURRED"
            in2="EDGE_MASK"
            operator="in"
            result="EDGE_ABERRATION"
          />
          <feComponentTransfer in="EDGE_MASK" result="INVERTED_MASK">
            <feFuncA type="table" tableValues="1 0" />
          </feComponentTransfer>
          <feComposite in="CENTER_ORIGINAL" in2="INVERTED_MASK" operator="in" result="CENTER_CLEAN" />
          <feComposite in="EDGE_ABERRATION" in2="CENTER_CLEAN" operator="over" />
        </filter>
      </template>
    </defs>
  </svg>
</template>

<style scoped>
.glass-defs {
  position: absolute;
  width: 0;
  height: 0;
  overflow: hidden;
  pointer-events: none;
}
</style>

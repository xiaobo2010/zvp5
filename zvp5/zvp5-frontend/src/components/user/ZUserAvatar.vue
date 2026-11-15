<script setup lang="ts">
import { computed, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 姓名。空值回退问号，只取首字。 */
    name?: string | null
    /** 学号 / 账号 id。只用来做编号和条纹，不展示明文。 */
    code?: string | number | null
    /** xs 导航 30px；md 身份卡 56px；lg 资料卡 72px。 */
    size?: 'xs' | 'md' | 'lg'
  }>(),
  {
    name: '',
    code: '',
    size: 'md'
  }
)

const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')

const seed = computed(() => {
  let hash = 2166136261
  const source = String(props.code ?? '').trim() || String(props.name ?? '').trim()
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
})

const initial = computed(() => {
  const text = String(props.name ?? '')
    .trim()
    .replace(/^\uFEFF/, '')
  return [...text][0] || '?'
})

/** 三位短号。xs 不画出来，但仍挂在 data 上，方便核对同一个人是否同纹。 */
const serial = computed(() => String(seed.value % 1000).padStart(3, '0'))

const digits = computed(() => String(props.code ?? '').replace(/\D/g, ''))

/** 条纹按学号尾号定宽窄；没有数字时退回短号，避免空白学号全员同一条。 */
const bars = computed(() => {
  const source = (digits.value || serial.value).slice(-4).padStart(4, '0')
  return [...source].map((digit, index) => ({
    key: index,
    wide: Number(digit) >= 5
  }))
})

/** 内框左右刻度只开其中一侧，避免 56px 上左右对称成装饰贴纸。 */
const railSide = computed(() => (seed.value % 2 === 0 ? 'left' : 'right'))
</script>

<template>
  <span
    class="z-pilot-plate"
    :class="[`z-pilot-plate--${size}`]"
    :data-serial="serial"
    :data-rail="railSide"
    aria-hidden="true"
  >
    <svg class="z-pilot-plate__mark" viewBox="0 0 72 72" focusable="false">
      <defs>
        <pattern
          :id="`${uid}-scan`"
          width="72"
          height="4"
          patternUnits="userSpaceOnUse"
          :y="seed % 4"
        >
          <rect class="z-pilot-plate__scan-line" width="72" height="1" />
        </pattern>
      </defs>

      <!-- 导航上的 xs 是纯正圆盘：阴影交给 CSS drop-shadow，hover 时整体抬升。 -->
      <g v-if="size === 'xs'">
        <circle class="z-pilot-plate__plate" cx="36" cy="36" r="31" />
        <circle class="z-pilot-plate__scan" cx="36" cy="36" r="31" :fill="`url(#${uid}-scan)`" />
        <path class="z-pilot-plate__round-tick" d="M60.2 50.5A28 28 0 0 1 50.5 60.2" />
      </g>
      <g v-else>
        <!-- 硬阴影是一块实心错位，不用模糊投影。 -->
        <polygon class="z-pilot-plate__offset" points="9,6 65,6 73,14 73,74 17,74 9,66" />
        <polygon class="z-pilot-plate__plate" points="5,2 58,2 70,14 70,70 14,70 2,58" />
        <polygon
          class="z-pilot-plate__scan"
          points="5,2 58,2 70,14 70,70 14,70 2,58"
          :fill="`url(#${uid}-scan)`"
        />

        <polyline class="z-pilot-plate__inset" points="12,8 52,8 64,20 64,62 52,64 20,64 8,52 8,12" />
        <path class="z-pilot-plate__tick" d="M62 56h7v2.4H64.6V66" />

        <path v-if="railSide === 'left'" class="z-pilot-plate__rail" d="M12 22h7M12 27h4" />
        <path v-else class="z-pilot-plate__rail" d="M60 48h-7M60 43h-4" />
        <g class="z-pilot-plate__bars">
          <rect
            v-for="bar in bars"
            :key="bar.key"
            :x="13 + bar.key * 8"
            :y="bar.wide ? 63.4 : 64.6"
            :width="bar.wide ? 5 : 2.6"
            :height="bar.wide ? 4.2 : 3"
          />
        </g>
      </g>
    </svg>

    <span class="z-pilot-plate__initial">
      <span class="z-pilot-plate__ghost">{{ initial }}</span>
      <span class="z-pilot-plate__type">{{ initial }}</span>
    </span>
    <span v-if="size !== 'xs'" class="z-pilot-plate__serial">{{ serial }}</span>
  </span>
</template>

<style scoped>
/* 飞行员 ID 铭牌：切角外框 + 扫描线 + 首字 + 一处品红刻度。
   xs 只保留这四样；md/lg 才加内框刻度、尾号条纹和短号。 */
.z-pilot-plate {
  --plate-ink: var(--yg-color-primary, #0b9f93);
  --plate-pink: var(--phi-pink, #ff4e9b);
  --plate-face: color-mix(in srgb, var(--plate-ink) 14%, var(--yg-bg-surface, #0c0e12));
  --plate-shadow: color-mix(in srgb, var(--plate-ink) 42%, #050608);
  position: relative;
  display: inline-grid;
  place-items: center;
  flex: 0 0 auto;
  width: 56px;
  height: 56px;
  color: var(--plate-ink);
  overflow: visible;
}

.z-pilot-plate--xs {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  transition: transform 170ms ease;
}

.z-pilot-plate--xs .z-pilot-plate__mark {
  /* 阴影只跟圆盘走，别把首字也拓一层硬影。 */
  filter: drop-shadow(2px 2px 0 var(--plate-shadow));
  transition: filter 170ms ease;
}

.z-pilot-plate--lg {
  width: 72px;
  height: 72px;
}

.z-pilot-plate__mark {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.z-pilot-plate__offset {
  fill: var(--plate-shadow);
}

.z-pilot-plate__plate {
  fill: var(--plate-face);
  stroke: var(--plate-ink);
  stroke-width: 1.6;
  stroke-linejoin: miter;
}

.z-pilot-plate--xs .z-pilot-plate__plate {
  stroke-width: 2.6;
}

.z-pilot-plate__scan-line {
  fill: color-mix(in srgb, var(--plate-ink) 34%, transparent);
}

.z-pilot-plate__scan {
  opacity: 0.45;
  pointer-events: none;
}

.z-pilot-plate--xs .z-pilot-plate__scan {
  opacity: 0.28;
}

.z-pilot-plate__inset,
.z-pilot-plate__rail {
  fill: none;
  stroke: color-mix(in srgb, var(--plate-ink) 62%, transparent);
  stroke-width: 1.15;
  stroke-linecap: square;
  stroke-linejoin: miter;
}

.z-pilot-plate--xs .z-pilot-plate__inset {
  display: none;
}

.z-pilot-plate__tick {
  fill: none;
  stroke: var(--plate-pink);
  stroke-width: 1.8;
  stroke-linecap: square;
  stroke-linejoin: miter;
}

.z-pilot-plate--xs .z-pilot-plate__tick {
  stroke-width: 2.8;
}

/* xs 圆盘右下的品红短弧，替代切角铭牌的外角刻度。 */
.z-pilot-plate__round-tick {
  fill: none;
  stroke: var(--plate-pink);
  stroke-width: 2.8;
  stroke-linecap: butt;
}

.z-pilot-plate__bars rect {
  fill: var(--plate-ink);
}

.z-pilot-plate__initial {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  margin-top: -4px;
  color: var(--plate-ink);
  font-family: var(--phi-display, Bebas, Impact, sans-serif);
  font-size: 32px;
  font-weight: 400;
  line-height: 0.85;
  letter-spacing: 0;
}

.z-pilot-plate--xs .z-pilot-plate__initial {
  margin-top: -1px;
  font-family: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
}

.z-pilot-plate--lg .z-pilot-plate__initial {
  margin-top: -5px;
  font-size: 42px;
}

.z-pilot-plate__type,
.z-pilot-plate__ghost {
  grid-area: 1 / 1;
}

/* 幽灵层有 transform，会自己开层叠上下文；不显式压回去会盖住正文，
   hover 时尤其明显（深色字被粉色幽灵挡住）。 */
.z-pilot-plate__ghost {
  position: relative;
  z-index: 0;
  color: var(--plate-pink);
  opacity: 0.72;
  transform: translate(1.5px, -1px);
}

.z-pilot-plate__type {
  position: relative;
  z-index: 1;
}

.z-pilot-plate--xs .z-pilot-plate__ghost {
  opacity: 0.5;
  transform: translate(0.7px, -0.4px);
}

.z-pilot-plate__serial {
  position: absolute;
  z-index: 1;
  right: 8px;
  bottom: 6px;
  color: var(--yg-text-tertiary, #8d98a2);
  font-family: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  font-size: 8px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.06em;
}

.z-pilot-plate--lg .z-pilot-plate__serial {
  right: 10px;
  bottom: 8px;
  font-size: 10px;
}

html[data-theme='light'] .z-pilot-plate {
  --plate-pink: #c2186a;
  --plate-face: color-mix(in srgb, var(--plate-ink) 10%, #ffffff);
  --plate-shadow: #101418;
}

/* 账号按钮里：hover / focus 把铭牌翻成主色实底，字改表面色。 */
:global(.yg-glass-nav__account:hover .z-pilot-plate),
:global(.yg-glass-nav__account:focus-visible .z-pilot-plate) {
  /* face 必须锁在主色上：若写 var(--plate-ink)，下一行把 ink 改成表面色后
     face 也会跟着变白，首字就和底板同色消失了。 */
  --plate-face: var(--yg-color-primary, #0b9f93);
  --plate-ink: var(--yg-bg-surface, #f4f6f8);
  --plate-shadow: #050608;
}

:global(.yg-glass-nav__account:focus-visible .z-pilot-plate__plate) {
  stroke: var(--yg-focus-ring-color, var(--yg-color-primary));
  stroke-width: 3.2;
}

/* 浅色主题的底板规则带 html[data-theme] 前缀，特异性更高；不补这一条
   会把 hover 的实底又压回浅色，深色字落在浅板上。 */
:global(html[data-theme='light'] .yg-glass-nav__account:hover .z-pilot-plate),
:global(html[data-theme='light'] .yg-glass-nav__account:focus-visible .z-pilot-plate) {
  --plate-face: var(--yg-color-primary, #007a70);
  --plate-ink: #ffffff;
  /* 浅色主题的基础规则带 html[data-theme] 前缀，阴影也要在这里抬回深色。 */
  --plate-shadow: #050608;
}

/* 圆盘单独做抬升：阴影跟着位移方向拉长，切角铭牌保持原来的平面感。 */
:global(.yg-glass-nav__account:hover .z-pilot-plate--xs),
:global(.yg-glass-nav__account:focus-visible .z-pilot-plate--xs) {
  /* 暗色主题里 #050608 会和页面底一样，圆盘 hover 就丢了投影；用主色压暗保持可见。 */
  --plate-shadow: color-mix(in srgb, var(--yg-color-primary, #0b9f93) 42%, #050608);
  transform: translate(-1px, -1px);
}

:global(.yg-glass-nav__account:hover .z-pilot-plate--xs .z-pilot-plate__mark),
:global(.yg-glass-nav__account:focus-visible .z-pilot-plate--xs .z-pilot-plate__mark) {
  filter: drop-shadow(3px 3px 0 var(--plate-shadow));
}
</style>

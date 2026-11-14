<script setup lang="ts">
import { computed } from 'vue'

/**
 * 卡片基座（对应规范 1/2/5/6/7/8/9/10/14/22/23）。
 * 结构统一：图标 → 标题 → 说明 → 按钮，全部左对齐，内边距 24。
 */
const props = withDefaults(
  defineProps<{
    variant?: 'plain' | 'glass' | 'liquid'
    border?: 'none' | 'grad' | 'spin' | 'marquee'
    spotlight?: boolean
    glow?: boolean
    /** 全息：表面彩虹反光跟着指针走 */
    holo?: boolean
    padding?: number
    radius?: number
    /** 说明最多两行，超出省略（规范 15） */
    clamp?: boolean
  }>(),
  {
    variant: 'glass',
    border: 'none',
    spotlight: false,
    glow: false,
    holo: false,
    padding: 24,
    radius: 26,
    clamp: true
  }
)

const style = computed(() => ({
  '--yg2-card-p': `${props.padding}px`,
  '--yg2-card-r': `${props.radius}px`,
  '--yg2-card-inner-r': `${Math.max(0, props.radius - props.padding)}px`
}))

const classes = computed(() => [
  'yg2-xcard',
  props.variant !== 'plain' && `yg2-card--${props.variant}`,
  props.border !== 'none' && `yg2-card--border-${props.border}`,
  props.spotlight && 'yg2-card--spotlight',
  props.glow && 'yg2-card--glow',
  props.holo && 'yg2-card--holo',
  props.clamp && 'yg2-xcard--clamp'
])
</script>

<template>
  <article :class="classes" :style="style">
    <span class="yg2-card__noise" aria-hidden="true" />
    <span v-if="variant === 'liquid'" class="yg2-card__spec" aria-hidden="true" />
    <span v-if="holo" class="yg2-card__holo" aria-hidden="true" />
    <span v-if="holo" class="yg2-card__sheen" aria-hidden="true" />
    <span v-if="spotlight" class="yg2-card__spot" aria-hidden="true" />
    <span v-if="glow" class="yg2-card__glow" aria-hidden="true" />

    <header v-if="$slots.icon || $slots.title" class="yg2-xcard__head">
      <span v-if="$slots.icon" class="yg2-xcard__icon">
        <slot name="icon" />
      </span>
      <div class="yg2-xcard__headtext">
        <h3 v-if="$slots.title" class="yg2-xcard__title">
          <slot name="title" />
        </h3>
        <p v-if="$slots.subtitle" class="yg2-xcard__subtitle">
          <slot name="subtitle" />
        </p>
      </div>
      <div v-if="$slots.aside" class="yg2-xcard__aside">
        <slot name="aside" />
      </div>
    </header>

    <p v-if="$slots.desc" class="yg2-xcard__desc">
      <slot name="desc" />
    </p>

    <slot />

    <footer v-if="$slots.foot" class="yg2-xcard__foot">
      <slot name="foot" />
    </footer>
  </article>
</template>

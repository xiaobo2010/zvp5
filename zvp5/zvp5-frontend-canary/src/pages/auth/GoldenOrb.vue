<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 登录页左侧的 Golden Light Universe：
 * 一颗半透明香槟金玻璃球体 + 三圈不同倾角的玻璃环 + 两条光带 + 少量轨道粒子。
 * 指针移动只写 --px / --py，视差由 CSS 过渡完成；离开后回到自然浮动。
 */
const root = ref<HTMLElement | null>(null)
let frame = 0
let pending: { x: number; y: number } | null = null

function flush() {
  frame = 0
  const el = root.value
  if (!el || !pending) return
  el.style.setProperty('--px', pending.x.toFixed(3))
  el.style.setProperty('--py', pending.y.toFixed(3))
  pending = null
}

function onPointerMove(event: PointerEvent) {
  const el = root.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  pending = {
    x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
    y: ((event.clientY - rect.top) / rect.height) * 2 - 1
  }
  if (!frame) frame = requestAnimationFrame(flush)
}

function reset() {
  const el = root.value
  if (!el) return
  el.style.setProperty('--px', '0')
  el.style.setProperty('--py', '0')
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  if (!window.matchMedia('(pointer: fine)').matches) return
  window.addEventListener('pointermove', onPointerMove, { passive: true })
  document.addEventListener('pointerleave', reset)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onPointerMove)
  document.removeEventListener('pointerleave', reset)
  if (frame) cancelAnimationFrame(frame)
  frame = 0
})
</script>

<template>
  <div ref="root" class="universe" aria-hidden="true">
    <div class="depth depth-halo">
      <span class="halo" />
    </div>

    <div class="depth depth-mid">
      <div class="orb-float">
        <span class="orb">
          <span class="orb-refract" />
          <span class="orb-shade" />
          <span class="orb-spec" />
        </span>
      </div>
      <span class="ring ring-a" />
      <span class="ring ring-b" />
      <span class="ring ring-c" />
    </div>

    <div class="depth depth-far">
      <span class="band band-a" />
      <span class="band band-b" />
    </div>

    <div class="depth depth-near">
      <span class="orbit orbit-1"><i /></span>
      <span class="orbit orbit-2"><i /></span>
      <span class="orbit orbit-3"><i /></span>
      <span class="orbit orbit-4"><i /></span>
    </div>
  </div>
</template>

<style scoped>
.universe {
  --orb-size: clamp(230px, 27vw, 372px);
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  contain: layout paint;
}

.depth {
  position: absolute;
  inset: 0;
  transition: transform 620ms var(--spring-soft);
  will-change: transform;
}

.depth-halo {
  transform: translate3d(calc(var(--px, 0) * 8px), calc(var(--py, 0) * 6px), 0);
}

.depth-mid {
  transform: translate3d(calc(var(--px, 0) * 16px), calc(var(--py, 0) * 12px), 0);
}

.depth-far {
  transform: translate3d(calc(var(--px, 0) * 24px), calc(var(--py, 0) * 18px), 0);
}

.depth-near {
  transform: translate3d(calc(var(--px, 0) * 34px), calc(var(--py, 0) * 26px), 0);
}

/* --- 外光晕 ------------------------------------------------------------- */
.halo {
  position: absolute;
  left: 68%;
  top: 52%;
  width: calc(var(--orb-size) * 2.2);
  aspect-ratio: 1;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(
    circle,
    color-mix(in srgb, var(--accent) 26%, transparent),
    color-mix(in srgb, var(--accent) 9%, transparent) 44%,
    transparent 68%
  );
  animation: halo-pulse 14s var(--ease-in-out) infinite alternate;
}

/* --- 玻璃球体 ----------------------------------------------------------- */
.orb-float {
  position: absolute;
  left: 68%;
  top: 52%;
  width: var(--orb-size);
  aspect-ratio: 1;
  animation: orb-float 12s var(--ease-in-out) infinite alternate;
}

.orb {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  border: 1px solid rgba(255, 246, 230, 0.48);
  background:
    radial-gradient(
      42% 36% at 33% 26%,
      rgba(255, 255, 255, 0.96),
      rgba(255, 246, 231, 0.5) 24%,
      transparent 56%
    ),
    radial-gradient(72% 68% at 64% 74%, rgba(226, 172, 98, 0.5), transparent 64%),
    linear-gradient(152deg, rgba(255, 246, 228, 0.92), rgba(231, 188, 120, 0.5) 46%, rgba(156, 106, 40, 0.62));
  box-shadow:
    inset 0 2px 10px rgba(255, 255, 255, 0.5),
    inset -20px -28px 74px rgba(120, 78, 22, 0.42),
    0 44px 96px -44px rgba(118, 78, 20, 0.6);
  -webkit-backdrop-filter: blur(3px) saturate(125%);
  backdrop-filter: blur(3px) saturate(125%);
}

.orb-refract {
  position: absolute;
  inset: 3%;
  border-radius: 50%;
  background: conic-gradient(
    from 200deg,
    transparent 0 20%,
    rgba(255, 255, 255, 0.52) 31%,
    transparent 44%,
    rgba(255, 233, 193, 0.4) 63%,
    transparent 76%
  );
  mix-blend-mode: screen;
  opacity: 0.7;
  animation: spin-z 38s linear infinite;
}

.orb-shade {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  box-shadow:
    inset 0 -18px 38px -12px rgba(110, 72, 20, 0.4),
    inset 14px 18px 46px -18px rgba(255, 255, 255, 0.65);
}

.orb-spec {
  position: absolute;
  left: 22%;
  top: 15%;
  width: 34%;
  height: 22%;
  border-radius: 50%;
  background: radial-gradient(
    50% 50% at 50% 50%,
    rgba(255, 255, 255, 0.78),
    rgba(255, 255, 255, 0.16) 62%,
    transparent 78%
  );
  transform: rotate(-18deg);
  filter: blur(1px);
}

/* --- 玻璃环 ------------------------------------------------------------- */
.ring {
  position: absolute;
  left: 68%;
  top: 52%;
  border-radius: 50%;
  border: 1px solid rgba(255, 238, 205, 0.34);
  box-shadow:
    0 0 22px rgba(255, 226, 172, 0.16),
    inset 0 0 18px rgba(255, 236, 198, 0.12);
  transform-origin: center;
  animation: spin-z 30s linear infinite;
}

.ring::before {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  background: conic-gradient(
    from 0deg,
    transparent 0 68%,
    rgba(255, 240, 210, 0.72) 78%,
    transparent 88%
  );
  -webkit-mask: radial-gradient(closest-side, transparent calc(100% - 2.4px), #000 calc(100% - 2px));
  mask: radial-gradient(closest-side, transparent calc(100% - 2.4px), #000 calc(100% - 2px));
}

.ring-a {
  width: calc(var(--orb-size) * 1.36);
  height: calc(var(--orb-size) * 1.36);
  margin-left: calc(var(--orb-size) * -0.68);
  margin-top: calc(var(--orb-size) * -0.68);
  transform: rotateX(70deg) rotateZ(6deg);
  animation-duration: 26s;
}

.ring-b {
  width: calc(var(--orb-size) * 1.6);
  height: calc(var(--orb-size) * 1.6);
  margin-left: calc(var(--orb-size) * -0.8);
  margin-top: calc(var(--orb-size) * -0.8);
  transform: rotateX(62deg) rotateY(20deg);
  animation-duration: 44s;
  animation-direction: reverse;
  opacity: 0.75;
}

.ring-c {
  width: calc(var(--orb-size) * 1.86);
  height: calc(var(--orb-size) * 1.86);
  margin-left: calc(var(--orb-size) * -0.93);
  margin-top: calc(var(--orb-size) * -0.93);
  transform: rotateX(74deg) rotateY(-16deg);
  animation-duration: 58s;
  opacity: 0.55;
}

/* --- 光带：只保留一细段弧，避免整圈发亮 -------------------------------- */
.band {
  position: absolute;
  left: 68%;
  top: 52%;
  border-radius: 50%;
  -webkit-mask: radial-gradient(closest-side, transparent 61%, #000 61.4%, #000 62.2%, transparent 62.6%);
  mask: radial-gradient(closest-side, transparent 61%, #000 61.4%, #000 62.2%, transparent 62.6%);
  animation: spin-z 46s linear infinite;
}

.band-a {
  width: calc(var(--orb-size) * 2.1);
  height: calc(var(--orb-size) * 2.1);
  margin-left: calc(var(--orb-size) * -1.05);
  margin-top: calc(var(--orb-size) * -1.05);
  background: conic-gradient(
    from 20deg,
    transparent 0 62%,
    rgba(255, 236, 198, 0.4) 72%,
    rgba(255, 224, 168, 0.1) 80%,
    transparent 88%
  );
}

.band-b {
  width: calc(var(--orb-size) * 2.44);
  height: calc(var(--orb-size) * 2.44);
  margin-left: calc(var(--orb-size) * -1.22);
  margin-top: calc(var(--orb-size) * -1.22);
  background: conic-gradient(
    from 200deg,
    transparent 0 54%,
    rgba(255, 244, 220, 0.28) 66%,
    transparent 78%
  );
  animation-duration: 68s;
  animation-direction: reverse;
  opacity: 0.7;
}

/* --- 轨道粒子 ----------------------------------------------------------- */
.orbit {
  position: absolute;
  left: 68%;
  top: 52%;
  width: 0;
  height: 0;
  animation: spin-z 22s linear infinite;
}

.orbit i {
  position: absolute;
  left: 0;
  top: 0;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #fff8e9, #e6be7a 62%, rgba(206, 148, 62, 0));
  box-shadow: 0 0 9px rgba(242, 200, 126, 0.75);
  transform: translate(-50%, -50%) translateY(calc(var(--r) * -1));
}

.orbit-1 {
  --r: calc(var(--orb-size) * 0.68);
  animation-duration: 18s;
}

.orbit-2 {
  --r: calc(var(--orb-size) * 0.86);
  animation-duration: 27s;
  animation-direction: reverse;
  animation-delay: -6s;
}

.orbit-2 i {
  width: 4px;
  height: 4px;
  opacity: 0.8;
}

.orbit-3 {
  --r: calc(var(--orb-size) * 1.02);
  animation-duration: 34s;
  animation-delay: -12s;
}

.orbit-3 i {
  width: 3.5px;
  height: 3.5px;
  opacity: 0.65;
}

.orbit-4 {
  --r: calc(var(--orb-size) * 0.58);
  animation-duration: 15s;
  animation-direction: reverse;
  animation-delay: -3s;
}

.orbit-4 i {
  width: 3px;
  height: 3px;
  opacity: 0.55;
}

/* --- 动效 --------------------------------------------------------------- */
@keyframes spin-z {
  to {
    rotate: 360deg;
  }
}

@keyframes orb-float {
  from {
    transform: translate(-50%, -50%) translate3d(0, -12px, 0) rotate(-1.2deg);
  }

  to {
    transform: translate(-50%, -50%) translate3d(0, 10px, 0) rotate(1.4deg);
  }
}

@keyframes halo-pulse {
  from {
    transform: translate(-50%, -50%) scale(1);
    opacity: 0.85;
  }

  to {
    transform: translate(-50%, -50%) scale(1.07);
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .depth {
    transform: none;
    transition: none;
  }

  .orb-float,
  .halo,
  .ring,
  .band,
  .orbit,
  .orb-refract {
    animation: none;
  }

  .orb-float {
    transform: translate(-50%, -50%);
  }

  .halo {
    transform: translate(-50%, -50%);
  }
}

@media (max-width: 900px) {
  .universe {
    display: none;
  }
}
</style>

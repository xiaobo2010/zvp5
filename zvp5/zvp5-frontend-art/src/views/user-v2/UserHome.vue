<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useStorage } from '@vueuse/core'
import { RouterLink } from 'vue-router'
import { useUserStore } from '@/stores/user'
import {
  ART_PIECES,
  getArtPiece,
  getArtProgress,
  artPixelOrder,
  createArtFrame,
  stableHash
} from '@/utils/artHome'
import type { ArtProgress } from '@/utils/artHome'

const user = useUserStore()
const canvas = ref<HTMLCanvasElement | null>(null)
const imageFailed = ref(false)
const fullFrame = useStorage('zv.art.full-frame', false)
const breathing = useStorage('zv.art.breathing', false)
const busy = ref(false)
const status = ref('')
const seed = computed(() => (user.isLogin ? stableHash(user._id || String(user.id)) : 20261125))
const artwork = computed(() =>
  user.isLogin ? getArtPiece(user._id || String(user.id)) : ART_PIECES[1]!
)
const progress = computed<ArtProgress[]>(() =>
  user.isLogin
    ? getArtProgress(user.time)
    : [
        { key: 'on', label: '校内义工', value: 16, target: 25, ratio: 0.64 },
        { key: 'off', label: '校外义工', value: 8, target: 15, ratio: 0.53 },
        { key: 'social', label: '社会实践', value: 9, target: 18, ratio: 0.5 }
      ]
)
const percent = (value: number) => `${Math.round(value * 100)}%`
const totalHours = computed(() =>
  (
    Number(user.time.onCampus || 0) +
    Number(user.time.offCampus || 0) +
    Number(user.time.socialPractice || 0)
  ).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
)
let picture: HTMLImageElement | null = null
let source: ImageData | null = null
let frame: ReturnType<typeof createArtFrame> | null = null
let displayed: ImageData | null = null
let sequence = 0
let alive = false
let timer = 0
let raf = 0
let animatePair: ((phase: number) => void) | null = null

function draw() {
  if (displayed) canvas.value?.getContext('2d')?.putImageData(displayed, 0, 0)
}
function stopBreath() {
  clearTimeout(timer)
  cancelAnimationFrame(raf)
  if (animatePair) {
    animatePair(1)
    animatePair = null
    draw()
  }
}
function scheduleBreath() {
  stopBreath()
  if (!alive || !breathing.value || document.hidden || !frame) return
  timer = window.setTimeout(
    () => {
      animatePair = frame?.beginBreath() ?? null
      if (!animatePair) return
      const start = performance.now()
      let lastDraw = -Infinity
      function tick(now: number) {
        if (!animatePair) return
        const phase = Math.min(1, (now - start) / 1800)
        if (now - lastDraw >= 50 || phase === 1) {
          animatePair(phase)
          draw()
          lastDraw = now
        }
        if (phase < 1) raf = requestAnimationFrame(tick)
        else {
          animatePair = null
          scheduleBreath()
        }
      }
      raf = requestAnimationFrame(tick)
    },
    4500 + Math.random() * 3500
  )
}
function render() {
  stopBreath()
  if (!source) return
  const order = artPixelOrder(source.width, source.height, seed.value)
  frame = createArtFrame(source.data, order, progress.value)
  displayed = new ImageData(frame.pixels, source.width, source.height)
  draw()
  scheduleBreath()
}
function loadArtwork() {
  const ticket = ++sequence
  stopBreath()
  source = frame = displayed = null
  if (picture) picture.onload = picture.onerror = null
  const image = new Image()
  picture = image
  image.crossOrigin = 'anonymous'
  image.decoding = 'async'
  image.onload = () => {
    const target = canvas.value
    const ctx = target?.getContext('2d')
    if (!alive || ticket !== sequence || !target || !ctx) return
    // Bound both axes on tablets, including narrow portrait originals.
    const scale = Math.min(1, 768 / Math.max(image.naturalWidth, image.naturalHeight))
    target.width = Math.max(1, Math.round(image.naturalWidth * scale))
    target.height = Math.max(1, Math.round(image.naturalHeight * scale))
    ctx.drawImage(image, 0, 0, target.width, target.height)
    try {
      source = ctx.getImageData(0, 0, target.width, target.height)
      imageFailed.value = false
      render()
    } catch {
      imageFailed.value = true
    }
  }
  image.onerror = () => {
    if (ticket === sequence) imageFailed.value = true
  }
  image.src = artwork.value.src
}
async function refresh() {
  if (busy.value || !user.isLogin) return
  busy.value = true
  status.value = ''
  try {
    await user.getUserActivityTime()
    status.value = '进度已更新。'
  } catch {
    status.value = '暂时无法更新，已保留当前画面。'
  } finally {
    busy.value = false
  }
}
watch(() => artwork.value.src, loadArtwork)
watch([progress, seed], render)
watch(breathing, scheduleBreath)
onMounted(() => {
  alive = true
  document.addEventListener('visibilitychange', scheduleBreath)
  loadArtwork()
  if (user.isLogin)
    void user.getUserActivityTime().catch(() => {
      status.value = '义工时长暂时无法读取。'
    })
})
onBeforeUnmount(() => {
  alive = false
  document.removeEventListener('visibilitychange', scheduleBreath)
  stopBreath()
  sequence++
  if (picture) {
    picture.onload = null
    picture.onerror = null
  }
})
</script>

<template>
  <main class="art-home" :class="{ 'art-home--full': fullFrame }">
    <div class="art-stage" aria-label="义工完成情况画作">
      <canvas
        ref="canvas"
        class="art-stage__canvas"
        :class="{ 'art-stage__canvas--failed': imageFailed }"
        role="img"
        :aria-label="`义工完成情况画作；${progress.map((item) => `${item.label}${percent(item.ratio)}`).join('，')}`"
      />
      <div v-if="imageFailed" class="art-stage__fallback">画作暂时无法显示</div>
    </div>
    <header class="art-topline">
      <RouterLink class="art-wordmark" to="/" aria-label="Art 首页">ZV <i>·</i> ART</RouterLink>
      <span class="art-topline__edition">VOLUNTEER · IN PROGRESS</span>
      <div class="art-topline__links">
        <RouterLink v-if="user.isLogin" to="/activities">活动</RouterLink>
        <RouterLink v-if="user.isLogin" to="/me">我的资料</RouterLink>
        <RouterLink v-if="user.isLogin" to="/notifications">通知</RouterLink>
        <RouterLink
          v-if="user.position.some((role) => ['admin', 'department', 'secretary'].includes(role))"
          to="/manage"
          >管理</RouterLink
        >
        <RouterLink v-if="!user.isLogin" to="/user/login">登录</RouterLink>
      </div>
    </header>
    <section class="art-caption" aria-live="polite">
      <p v-if="!user.isLogin" class="art-caption__guest">
        示例展画 · 登录后，画作会随你的义工进度逐渐显影
      </p>
      <p v-else class="art-caption__guest">
        {{ user.name }} <span class="art-caption__dot">·</span> 累计 {{ totalHours }} 小时
      </p>
    </section>
    <aside class="art-progress" :aria-label="user.isLogin ? '个人义工完成进度' : '示例进度'">
      <div
        v-for="(item, index) in progress"
        :key="item.key"
        class="art-progress__item"
        :class="`art-progress__item--${item.key}`"
        :style="{ '--item': index }"
      >
        <span class="art-progress__swatch" aria-hidden="true"></span
        ><span class="art-progress__label">{{ item.label }}</span>
        <span class="art-progress__bar" aria-hidden="true"
          ><i :style="{ width: percent(item.ratio) }"></i></span
        ><span class="art-progress__number">{{ percent(item.ratio) }}</span>
      </div>
      <p v-if="user.isLogin" class="art-progress__hours">
        {{ progress.map((item) => `${item.value}/${item.target}h`).join('　') }}
      </p>
    </aside>
    <footer class="art-controls">
      <button
        type="button"
        class="art-control"
        :aria-pressed="fullFrame"
        @click="fullFrame = !fullFrame"
      >
        {{ fullFrame ? '铺满画面' : '看全幅' }}
      </button>
      <button
        type="button"
        class="art-control"
        :aria-pressed="breathing"
        @click="breathing = !breathing"
      >
        呼吸{{ breathing ? '：开' : '：关' }}
      </button>
      <button
        v-if="user.isLogin"
        type="button"
        class="art-control"
        :disabled="busy"
        @click="refresh"
      >
        {{ busy ? '更新中…' : '刷新进度' }}
      </button>
      <span v-if="status" role="status" class="art-status">{{ status }}</span>
      <span class="art-controls__credit">像素逐渐显影</span>
    </footer>
  </main>
</template>

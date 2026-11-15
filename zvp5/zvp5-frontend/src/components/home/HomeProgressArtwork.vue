<script setup lang="ts">
// 固定逻辑画布保证窗口变化不会重新分配已揭示像素；物理左、中、右依次对应三类义工。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  buildRevealRects,
  type HomeArtworkAssignment,
  type HomeCategory
} from '@/utils/homeArtwork'

const props = defineProps<{
  assignment: HomeArtworkAssignment | null
  categories: HomeCategory[]
  loading?: boolean
}>()
const CANVAS_MAX_WIDTH = 960
const CANVAS_MAX_HEIGHT = 1280
const FALLBACK_RATIO = 123 / 238
const CATEGORY_KEYS: HomeCategory['key'][] = ['on', 'off', 'social']
const POSITION_LABELS = ['左', '中', '右']

const canvas = ref<HTMLCanvasElement | null>(null)
const imageError = ref(false)
const imageLoading = ref(false)
let image: HTMLImageElement | null = null
let imageSource = ''
let pending: HTMLImageElement | null = null
let version = 0
let mounted = false
let observer: MutationObserver | undefined
let media: MediaQueryList | undefined
let mediaUsesLegacyListener = false

const artworkRatio = computed(() => {
  const art = props.assignment?.artwork
  return art &&
    Number.isFinite(art.width) &&
    Number.isFinite(art.height) &&
    art.width > 0 &&
    art.height > 0
    ? art.width / art.height
    : FALLBACK_RATIO
})

const dimensions = computed(() => {
  const art = props.assignment?.artwork
  if (!art || !(art.width > 0) || !(art.height > 0)) return { width: 123, height: 238 }
  const scale = Math.min(1, CANVAS_MAX_WIDTH / art.width, CANVAS_MAX_HEIGHT / art.height)
  return {
    width: Math.max(3, Math.floor(art.width * scale)),
    height: Math.max(1, Math.floor(art.height * scale))
  }
})

const categoriesByKey = computed(
  () => new Map(props.categories.map((category) => [category.key, category]))
)
const orderedCategories = computed(() =>
  CATEGORY_KEYS.map((key) => categoriesByKey.value.get(key) ?? null)
)
const allCategoriesAreKnownZero = computed(() =>
  orderedCategories.value.every(
    (category) => category && Number.isFinite(category.ratio) && category.ratio <= 0
  )
)

const label = computed(() => {
  if (!props.assignment) return '义工时长作品等待账号数据'
  if (!props.categories.length) return '义工时长作品等待时长数据，暂未显示进度'
  const regions = orderedCategories.value.map((category, index) => {
    if (!category || !Number.isFinite(category.ratio))
      return `${POSITION_LABELS[index]}侧区域等待时长数据`
    const value = Number((Math.max(0, Math.min(1, category.ratio)) * 100).toFixed(2))
    return `${POSITION_LABELS[index]}侧${category.label}揭示${value.toLocaleString('zh-CN')}%`
  })
  return `义工时长作品；${regions.join('；')}`
})

function draw() {
  const target = canvas.value
  const ctx = target?.getContext('2d')
  if (!target || !ctx) return

  const { width, height } = dimensions.value
  target.width = width
  target.height = height
  const surface = getComputedStyle(target).getPropertyValue('--yg-bg-surface-sunken').trim()
  if (surface) ctx.fillStyle = surface
  ctx.fillRect(0, 0, width, height)
  const sourceImage = image
  if (!sourceImage || !props.assignment) return

  // 每次只把已揭示像素裁入画布；隐藏区域保持实色，原图不会作为底图泄漏。
  orderedCategories.value.forEach((category, index) => {
    if (!category || !Number.isFinite(category.ratio) || category.ratio <= 0) return
    const left = Math.floor((index * width) / 3)
    const right = Math.floor(((index + 1) * width) / 3)
    const stripWidth = right - left
    if (stripWidth < 1) return
    const rects = buildRevealRects(
      stripWidth,
      height,
      category.ratio,
      props.assignment!.seed ^ Math.imul(index + 1, 0x9e3779b9)
    )
    if (!rects.length) return

    ctx.save()
    ctx.beginPath()
    for (const rect of rects) ctx.rect(left + rect.x, rect.y, rect.width, rect.height)
    ctx.clip()
    ctx.drawImage(sourceImage, 0, 0, width, height)
    ctx.restore()
  })
}

function releasePending() {
  if (!pending) return
  pending.onload = null
  pending.onerror = null
  pending = null
}

function clearImage() {
  image = null
  imageSource = ''
}

function loadImage(options: { preserve?: boolean } = {}) {
  if (!mounted) return
  const ticket = ++version
  releasePending()
  imageError.value = false

  const source = props.assignment?.artwork.src ?? ''
  if (!source) {
    clearImage()
    imageLoading.value = false
    draw()
    return
  }
  if (image && imageSource === source) {
    imageLoading.value = false
    draw()
    return
  }
  if (!options.preserve) clearImage()
  draw()

  imageLoading.value = true
  const next = new Image()
  pending = next
  next.decoding = 'async'
  next.onload = () => {
    if (ticket !== version || !mounted) return
    image = next
    imageSource = source
    pending = null
    imageLoading.value = false
    draw()
  }
  next.onerror = () => {
    if (ticket !== version || !mounted) return
    pending = null
    imageLoading.value = false
    imageError.value = true
    draw()
  }
  next.src = source
}

function retryImage() {
  loadImage({ preserve: true })
}

function handleMediaChange() {
  draw()
}

watch(
  () => [props.assignment?.artwork.id, props.assignment?.artwork.src],
  () => loadImage(),
  { flush: 'post' }
)
watch(() => [props.assignment?.seed, props.categories], draw, { deep: true, flush: 'post' })

onMounted(() => {
  mounted = true
  loadImage()
  observer = new MutationObserver(draw)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'class']
  })
  media = window.matchMedia?.('(prefers-color-scheme: dark)')
  if (media && typeof media.addEventListener === 'function') {
    media.addEventListener('change', handleMediaChange)
  } else if (media && typeof media.addListener === 'function') {
    mediaUsesLegacyListener = true
    media.addListener(handleMediaChange)
  }
})

onBeforeUnmount(() => {
  mounted = false
  version++
  releasePending()
  observer?.disconnect()
  if (mediaUsesLegacyListener) media?.removeListener(handleMediaChange)
  else media?.removeEventListener?.('change', handleMediaChange)
})
</script>

<template>
  <figure class="yg-home-artwork" :style="{ '--yg-artwork-ratio': artworkRatio }">
    <div
      class="yg-home-artwork__frame"
      :aria-busy="imageLoading || Boolean(loading)"
      :data-artwork-id="assignment?.artwork.id"
    >
      <canvas ref="canvas" class="yg-home-artwork__canvas" role="img" :aria-label="label" />
      <div v-if="imageError" class="yg-home-artwork__state" role="status" aria-live="polite">
        <span>作品暂时未能加载</span>
        <button class="yg-btn yg-btn--secondary yg-btn--lg" type="button" @click="retryImage">
          重试图片
        </button>
      </div>
      <div
        v-else-if="!categories.length || imageLoading || !assignment"
        class="yg-home-artwork__state"
        role="status"
        aria-live="polite"
      >
        <span>{{
          imageLoading ? '正在准备你的作品…' : loading ? '正在读取义工时长…' : '等待时长数据'
        }}</span>
      </div>
      <div v-else-if="allCategoriesAreKnownZero" class="yg-home-artwork__state">
        <span>从第一次参与开始，点亮你的作品。</span>
      </div>
    </div>
    <figcaption v-if="assignment" class="yg-home-artwork__caption">
      <span>{{ assignment.artwork.title }} · {{ assignment.artwork.artist }}</span>
      <a
        class="yg-link"
        :href="assignment.artwork.sourceUrl"
        target="_blank"
        rel="noopener noreferrer"
        :aria-label="'查看' + assignment.artwork.title + '的图片来源'"
        >作品来源 ↗</a
      >
    </figcaption>
  </figure>
</template>

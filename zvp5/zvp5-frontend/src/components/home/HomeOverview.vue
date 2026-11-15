<script setup lang="ts">
// 首页展示层；正式首页与无后端预览共用同一组组件。
import { computed } from 'vue'
import { assignHomeArtwork, type HomeCategory, type HomeProgress } from '@/utils/homeArtwork'
import HomeProgressArtwork from './HomeProgressArtwork.vue'

const props = defineProps<{
  name: string
  number?: string | number
  userKey: string
  progress: HomeProgress | null
  loading?: boolean
  error?: string
}>()
defineEmits<{ refresh: []; details: []; profile: []; activities: [] }>()

const CATEGORY_KEYS: HomeCategory['key'][] = ['on', 'off', 'social']
const POSITION_LABELS = ['左', '中', '右']
const FALLBACK_RATIO = 123 / 238

interface ProgressSlot {
  key: HomeCategory['key']
  position: string
  category: HomeCategory | null
}

const assignment = computed(() => assignHomeArtwork(props.userKey))
const progressSlots = computed<ProgressSlot[]>(() => {
  const categoriesByKey = new Map(
    (props.progress?.categories ?? []).map((category) => [category.key, category])
  )
  return CATEGORY_KEYS.map((key, index) => ({
    key,
    position: POSITION_LABELS[index]!,
    category: categoriesByKey.get(key) ?? null
  }))
})
const orderedCategories = computed(() =>
  progressSlots.value.flatMap((slot) => (slot.category ? [slot.category] : []))
)
const artworkRatio = computed(() => {
  const art = assignment.value?.artwork
  return art &&
    Number.isFinite(art.width) &&
    Number.isFinite(art.height) &&
    art.width > 0 &&
    art.height > 0
    ? art.width / art.height
    : FALLBACK_RATIO
})

const format = (value: number) =>
  Number.isFinite(value) ? Number(value.toFixed(2)).toLocaleString('zh-CN') : '—'
const clampPercent = (value: number) =>
  Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0
const progressText = (category: HomeCategory) =>
  `${format(category.effective)} / ${format(category.target)} 小时`
const gapText = (category: HomeCategory) => {
  if (!Number.isFinite(category.gap)) return '完成情况暂不可用'
  return category.gap >= 0 ? '已达标' : `还差 ${format(-category.gap)} 小时`
}
</script>

<template>
  <section class="yg-home-overview" aria-labelledby="home-title">
    <header class="yg-home-overview__header yg-page-header">
      <div>
        <p class="yg-home-overview__eyebrow">我的义工足迹</p>
        <h1 id="home-title" class="yg-home-overview__title">每一小时，让美好显现。</h1>
      </div>
      <button
        type="button"
        class="yg-home-overview__profile yg-btn yg-btn--ghost yg-btn--lg"
        @click="$emit('profile')"
        aria-label="查看个人资料"
      >
        <span>{{ name || '同学' }}</span><span aria-hidden="true">↗</span>
      </button>
    </header>
    <div class="yg-home-overview__identity">
      <span v-if="number !== undefined && number !== null && number !== ''">{{ number }}</span><slot
        name="identity"
      />
    </div>

    <div class="yg-home-overview__art-stack" :style="{ '--yg-home-artwork-ratio': artworkRatio }">
      <HomeProgressArtwork
        :assignment="assignment"
        :categories="orderedCategories"
        :loading="loading"
      />
      <div
        v-if="progress"
        class="yg-home-overview__progress"
        aria-label="义工完成进度，三栏从左到右对应作品左侧、中部与右侧"
      >
        <div
          v-for="slot in progressSlots"
          :key="slot.key"
          class="yg-home-overview__category"
          :data-category="slot.key"
        >
          <template v-if="slot.category">
            <p class="yg-home-overview__category-label">
              <span>{{ slot.position }} · </span>{{ slot.category.label }}
            </p>
            <p class="yg-home-overview__percent yg-text-num">
              {{ format(slot.category.percent) }}<small>%</small>
            </p>
            <div
              class="yg-home-overview__track"
              role="progressbar"
              :aria-label="slot.category.label"
              :aria-valuenow="clampPercent(slot.category.percent)"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuetext="progressText(slot.category)"
            >
              <span :style="{ width: clampPercent(slot.category.ratio * 100) + '%' }"></span>
            </div>
            <p class="yg-home-overview__hours yg-text-num">{{ progressText(slot.category) }}</p>
            <p class="yg-home-overview__gap">
              {{ gapText(slot.category)
              }}<span v-if="slot.category.bonus"> · 含折算 {{ format(slot.category.bonus) }}</span>
            </p>
          </template>
          <template v-else>
            <p class="yg-home-overview__category-label">
              <span>{{ slot.position }} · </span>暂未取得
            </p>
            <p class="yg-home-overview__percent yg-text-num">—</p>
            <p class="yg-home-overview__hours">该项时长未知</p>
          </template>
        </div>
      </div>
      <p v-else class="yg-home-overview__note">
        取得时长后，校内、校外与社会实践将分别点亮作品的左、中、右部分。
      </p>
    </div>

    <p v-if="error" class="yg-home-overview__error" role="status" aria-live="polite">{{ error }}</p>
    <div class="yg-home-overview__actions">
      <p class="yg-home-overview__total">
        累计参与
        <strong class="yg-text-num">{{ progress ? format(progress.total) : '—' }}</strong> 小时
      </p>
      <div class="yg-home-overview__tools">
        <button
          class="yg-btn yg-btn--ghost yg-btn--lg"
          type="button"
          :disabled="loading"
          @click="$emit('refresh')"
        >
          {{ loading ? '读取中…' : '刷新时长' }}
        </button>
        <button class="yg-btn yg-btn--secondary yg-btn--lg" type="button" @click="$emit('details')">
          查看明细
        </button>
        <button
          class="yg-btn yg-btn--primary yg-btn--lg"
          type="button"
          @click="$emit('activities')"
        >
          参与活动 ↗
        </button>
      </div>
    </div>
    <p class="yg-home-overview__note">
      {{
        progress?.hasBonus
          ? '进度含互补折算：校内超出 25 小时的部分按 1/3 计入校外，校外超出 15 小时的部分按 1/2 计入校内，四舍五入取整。累计时长不含折算。'
          : '每项完成多少，就揭示对应区域多少像素。作品与你的账号固定相伴。'
      }}
    </p>
    <slot name="footer" />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import {
  BookOpen,
  CalendarDays,
  Sparkles,
  Users,
  ArrowRight,
  Check,
  Clock
} from 'lucide-vue-next'
import YgCard from '@/ui/card/YgCard.vue'
import MetalCard from '@/ui/card/MetalCard.vue'
import TiltCard from '@/ui/card/TiltCard.vue'
import FlipCard from '@/ui/card/FlipCard.vue'

/** 共享元素转场（规范 13）：点击卡片用 View Transitions 放大成详情。 */
const expanded = ref<null | { title: string; desc: string; kind: string }>(null)
/** 最近一次点开的卡片下标：只有它能挂 view-transition-name，避免重名 */
const sharedIndex = ref<number | null>(null)
const detailRef = ref<HTMLElement | null>(null)

const samples = [
  { title: '社区图书馆整理', desc: '整理书架、登记借阅，2 小时一场。', kind: '校外', icon: BookOpen },
  { title: '敬老院陪伴', desc: '陪老人聊天、读报，周末上午出发。', kind: '社会实践', icon: Users },
  { title: '外语节志愿服务', desc: '引导来宾、维持秩序，需提前一小时到场。', kind: '校内', icon: CalendarDays }
]

/** 共享元素转场：卡片与详情共用同一个 view-transition-name，浏览器负责形变。 */
async function withTransition(mutate: () => Promise<void> | void) {
  const doc = document as Document & { startViewTransition?: (cb: () => Promise<void> | void) => { finished: Promise<void> } }
  if (typeof doc.startViewTransition !== 'function') {
    await mutate()
    return
  }
  const transition = doc.startViewTransition(mutate)
  await transition.finished.catch(() => {})
}

async function expand(item: (typeof samples)[number], index: number) {
  sharedIndex.value = index
  await withTransition(() => {
    expanded.value = { title: item.title, desc: item.desc, kind: item.kind }
  })
}

async function collapse() {
  await withTransition(() => {
    expanded.value = null
  })
  sharedIndex.value = null
}
</script>

<template>
  <div class="yg2-page cardlab">
    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">设计系统</p>
        <h1 class="yg2-page-header__title">卡片规范</h1>
        <p class="yg2-page-header__subtitle">
          底子（排版 / 网格 / 纹理 / 边缘）→ 材质 → 光效 → 动效，逐层叠上去。
        </p>
      </div>
    </header>

    <!-- ============ 底子 ============ -->
    <h2 class="cardlab__h2">底子</h2>
    <div class="yg2-card-grid">
      <YgCard v-for="item in samples" :key="item.title">
        <template #icon><component :is="item.icon" :size="20" :stroke-width="1.6" /></template>
        <template #title>{{ item.title }}</template>
        <template #subtitle>{{ item.kind }}</template>
        <template #desc>{{ item.desc }}</template>
        <template #foot>
          <button class="yg2-btn yg2-btn--primary yg2-btn--sm" type="button">
            报名 <ArrowRight :size="15" :stroke-width="1.8" />
          </button>
          <span class="cardlab__meta"><Clock :size="13" :stroke-width="1.8" /> 2 小时</span>
        </template>
      </YgCard>
    </div>

    <!-- ============ 材质 ============ -->
    <h2 class="cardlab__h2">材质</h2>
    <div class="yg2-card-grid">
      <YgCard variant="glass">
        <template #icon><Sparkles :size="20" :stroke-width="1.6" /></template>
        <template #title>毛玻璃</template>
        <template #desc>背景模糊 20、白色 15%、一圈白色细边，背后垫彩色光斑。</template>
      </YgCard>

      <YgCard variant="liquid">
        <template #icon><Sparkles :size="20" :stroke-width="1.6" /></template>
        <template #title>液态玻璃</template>
        <template #desc>边缘带折射与镜面高光，指针移动时高光跟着走。</template>
      </YgCard>

      <YgCard variant="glass" holo spotlight>
        <template #icon><Sparkles :size="20" :stroke-width="1.6" /></template>
        <template #title>全息卡片</template>
        <template #desc>表面的彩虹反光跟着鼠标移动。</template>
      </YgCard>

      <MetalCard>
        <p class="metal__label">VOLUNTEER</p>
        <p class="metal__name">义工平台 · 成员卡</p>
        <p class="metal__no">NO. 2025 0515</p>
      </MetalCard>
    </div>

    <!-- ============ 光效 ============ -->
    <h2 class="cardlab__h2">光效</h2>
    <div class="yg2-card-grid">
      <YgCard border="grad">
        <template #title>渐变描边</template>
        <template #desc>品牌色过渡到透明的描边。</template>
      </YgCard>

      <YgCard border="spin">
        <template #title>流光边框</template>
        <template #desc>渐变边框 8 秒转一圈。</template>
      </YgCard>

      <YgCard border="marquee">
        <template #title>跑马灯描边</template>
        <template #desc>暗灰边框上有一小段亮光，4 秒跑一圈。</template>
      </YgCard>

      <YgCard spotlight>
        <template #title>聚光灯</template>
        <template #desc>鼠标位置亮一团柔光，边框跟着亮。</template>
      </YgCard>

      <YgCard glow>
        <template #title>光晕</template>
        <template #desc>卡片背后一团品牌色光晕，模糊 60、透明度 30%。</template>
      </YgCard>

      <YgCard>
        <template #title>发光分割线</template>
        <template #desc>中间亮、两头渐隐。</template>
        <hr class="yg2-divider-glow" />
        <p class="cardlab__muted">分割线下方的内容。</p>
      </YgCard>
    </div>

    <!-- ============ 动效 ============ -->
    <h2 class="cardlab__h2">动效</h2>
    <div class="yg2-card-grid">
      <TiltCard>
        <YgCard variant="glass">
          <template #icon><Sparkles :size="20" :stroke-width="1.6" /></template>
          <template #title>3D 倾斜</template>
          <template #desc>最多 10 度，表面反光跟着指针走。</template>
        </YgCard>
      </TiltCard>

      <FlipCard>
        <template #front>
          <YgCard variant="glass">
            <template #icon><BookOpen :size="20" :stroke-width="1.6" /></template>
            <template #title>翻转卡片</template>
            <template #desc>点一下翻到背面看详情。</template>
          </YgCard>
        </template>
        <template #back>
          <YgCard variant="liquid">
            <template #title>背面</template>
            <template #desc>这里可以放报名条件、集合地点、负责人联系方式。</template>
            <template #foot>
              <span class="cardlab__meta"><Check :size="13" :stroke-width="2" /> 已读</span>
            </template>
          </YgCard>
        </template>
      </FlipCard>

      <YgCard
        class="cardlab__expandable"
        :style="{ viewTransitionName: sharedIndex === 0 ? 'card-shared' : 'none' }"
        @click="expand(samples[0]!, 0)"
      >
        <template #icon><Sparkles :size="20" :stroke-width="1.6" /></template>
        <template #title>共享元素转场</template>
        <template #desc>点击用 View Transitions 放大成详情，关闭时缩回原位。</template>
      </YgCard>
    </div>

    <h2 class="cardlab__h2">组合</h2>
    <div class="yg2-card-grid">
      <YgCard :radius="28" :padding="16">
        <div class="yg2-xcard__inset yg2-grad-brand cardlab__grad">
          <span class="cardlab__grad-label">同色渐变</span>
          <strong class="cardlab__grad-value">9 / 25 小时</strong>
        </div>
        <template #title>同色渐变 + 嵌套圆角</template>
        <template #desc>
          品牌色从深到浅，内层圆角 = 外层 28 − 内边距 16 = 12。
        </template>
      </YgCard>

      <YgCard :padding="20" :radius="26">
        <template #icon><Clock :size="20" :stroke-width="1.6" /></template>
        <template #title>两行截断</template>
        <template #desc>
          说明超过两行就省略号收掉，按钮永远贴在卡片底部，同一排卡片高度保持一致，不会因为文案长短参差。
        </template>
        <template #foot>
          <button class="yg2-btn yg2-btn--secondary yg2-btn--sm" type="button">查看详情</button>
        </template>
      </YgCard>
    </div>

    <!-- 共享元素详情 -->
    <Teleport to="body">
      <div v-if="expanded" class="cardlab__overlay" @click.self="collapse">
        <div ref="detailRef" class="cardlab__detail" style="view-transition-name: card-shared">
          <YgCard variant="plain" :radius="28" :padding="28" class="cardlab__detailcard">
            <template #title>{{ expanded.title }}</template>
            <template #subtitle>{{ expanded.kind }}</template>
            <template #desc>{{ expanded.desc }}</template>
            <p class="cardlab__muted">
              这里演示共享元素：卡片本体用 view-transition-name 标记，展开与收起各跑一次转场。
            </p>
            <template #foot>
              <button class="yg2-btn yg2-btn--primary" type="button" @click="collapse">
                关闭
              </button>
            </template>
          </YgCard>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.cardlab {
  padding-bottom: 64px;
}

.cardlab__h2 {
  margin: 28px 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

.yg2-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  align-items: stretch;
}

.cardlab__meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.cardlab__muted {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: var(--yg2-text-tertiary);
}

.cardlab__grad {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 18px;
}

.cardlab__grad-label {
  font-size: 11px;
  letter-spacing: 0.08em;
  opacity: 0.82;
}

.cardlab__grad-value {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.metal__label {
  margin: 0 0 26px;
  font-size: 11px;
  letter-spacing: 0.18em;
}

.metal__name {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 700;
}

.metal__no {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.08em;
  opacity: 0.72;
}

.cardlab__expandable {
  cursor: pointer;
}

.cardlab__overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: color-mix(in srgb, #000 32%, transparent);
  backdrop-filter: blur(6px);
}

.cardlab__detail {
  width: min(460px, 100%);
}

/* 详情卡放在遮罩上，用实色避免「玻璃叠玻璃」发灰 */
.cardlab__detailcard {
  background: color-mix(in srgb, var(--yg2-surface) 94%, transparent);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  box-shadow: 0 30px 80px -20px color-mix(in srgb, #000 45%, transparent);
}
</style>

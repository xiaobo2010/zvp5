<script setup lang="ts">
import { useUserStore } from '@/stores/user'
import { ElButton, ElSkeleton, ElTag } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { ref } from 'vue'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { reactive, watch, computed } from 'vue'
import ZUserGroup from '@/components/tags/ZUserGroup.vue'
import ZUserPosition from '@/components/tags/ZUserPosition.vue'
import ZUserTimeJudge from '@/components/activity/ZUserTimeJudge.vue'
import { useWindowSize } from '@vueuse/core'
import { useRouter } from 'vue-router'

const header = useHeaderStore()
const user = useUserStore()
const { t } = useI18n()
const { width, height } = useWindowSize()
const router = useRouter()

if (!user.isLogin) {
  router.replace('/user/login')
}

header.setHeader(t('nav.home'))

const nowTime = dayjs().hour()
const greeting = ref(nowTime < 12 ? 'morning' : nowTime < 18 ? 'afternoon' : 'evening')
const loading = ref(false)

const time = reactive({
  socialPractice: user.time.socialPractice,
  onCampus: user.time.onCampus,
  offCampus: user.time.offCampus
})

const isPortrait = computed(() => width.value < height.value * 1.2)

async function refreshUser() {
  loading.value = true
  await user.refreshUser()
  loading.value = false
}

watch(
  () => user.isLogin,
  (v) => {
    if (v) {
      return
    }
    router.replace('/user/login')
  }
)
</script>

<template>
  <div class="px-20 fill home-wrap" style="width: 100%" v-if="user.isLogin">
    <p class="text-2xl py-8">
      {{ t('home.greeting', { greet: t('home.greetings.' + greeting), name: user.name }) }}
    </p>

    <!-- 个人信息 · 液态玻璃卡 -->
    <div class="py-4">
      <div class="lg-glass-card">
        <div class="lg-card-header">
          <p class="text-xl">{{ t('home.panels.information.title') }}</p>
          <ElButton
            type="success"
            :icon="Refresh"
            text
            bg
            circle
            @click="refreshUser"
            :disabled="loading"
          />
        </div>
        <ElSkeleton v-if="loading" :loading="true" :rows="3" />
        <div v-else class="lg-info-grid" :class="{ 'is-portrait': isPortrait }">
          <div class="lg-info-field">
            <span class="lg-info-label">{{ t('home.labels.name') }}</span>
            <span class="lg-info-value">{{ user.name }}</span>
          </div>
          <div class="lg-info-field">
            <span class="lg-info-label">{{ t('home.labels.number') }}</span>
            <span class="lg-info-value">{{ user.id }}</span>
          </div>
          <div class="lg-info-field">
            <span class="lg-info-label">{{ t('home.labels.identify') }}</span>
            <span class="lg-info-value lg-tag-group">
              <ZUserGroup
                v-for="group in user.groups"
                :key="group"
                class="px-1"
                :group="group"
                :grouping="false"
              />
              <ZUserPosition
                v-if="user.position.find((role) => role !== 'student') || !user.groups.length"
                :position="user.position"
              />
            </span>
          </div>
          <div class="lg-info-field">
            <span class="lg-info-label">{{ t('home.labels.status') }}</span>
            <span class="lg-info-value">
              <span v-if="!user.banned">{{ t('home.status.normal') }}</span>
              <ElTag v-else type="danger" size="small">{{ t('home.status.banned') }}</ElTag>
            </span>
          </div>
        </div>
        <div v-if="user.position.includes('secretary')" class="py-4">
          <ElButton text bg class="w-full" @click="$router.push('/group/' + user.class_id)">
            管理班级
          </ElButton>
        </div>
      </div>
    </div>

    <!-- 义工时间 · 液态玻璃卡（ZUserTimeJudge 内部统计项） -->
    <div :class="['py-4', user.shouldResetPassword ? 'z-blur' : '']">
      <ZUserTimeJudge
        :user="user._id"
        :on-campus="user.time.onCampus"
        :off-campus="time.offCampus"
        :social-practice="time.socialPractice"
        discount
      />
    </div>
  </div>
</template>

<style scoped>
.fill {
  width: 100%;
}
.z-blur {
  filter: blur(2rem);
}

/* ============ 个人信息玻璃卡 ============ */
.lg-glass-card {
  position: relative;
  border-radius: 24px;
  padding: 22px 24px;
  overflow: hidden;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.48));
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  backdrop-filter: blur(20px) saturate(150%);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    inset 0 1px 0 rgba(255, 255, 255, 0.7),
    0 6px 24px rgba(0, 0, 0, 0.08),
    0 16px 48px rgba(0, 0, 0, 0.06);
}

@supports (corner-shape: squircle) {
  .lg-glass-card {
    border-radius: 24px;
    corner-shape: squircle;
  }
}

html.dark .lg-glass-card {
  background: linear-gradient(135deg, rgba(38, 40, 52, 0.78), rgba(24, 26, 34, 0.6));
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.16),
    0 6px 24px rgba(0, 0, 0, 0.35),
    0 16px 48px rgba(0, 0, 0, 0.22);
}

/* 顶部镜面高光 */
.lg-glass-card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background: radial-gradient(140% 60% at 50% 0%, rgba(255, 255, 255, 0.5) 0%, transparent 55%);
  opacity: 0.5;
}

html.dark .lg-glass-card::before {
  background: radial-gradient(140% 60% at 50% 0%, rgba(255, 255, 255, 0.12) 0%, transparent 55%);
}

.lg-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.lg-card-header p {
  margin: 0;
  font-weight: 600;
}

/* 信息字段网格 */
.lg-info-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.lg-info-grid.is-portrait {
  grid-template-columns: 1fr;
}

.lg-info-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.5);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.5);
}

@supports (corner-shape: squircle) {
  .lg-info-field {
    corner-shape: squircle;
  }
}

html.dark .lg-info-field {
  background: rgba(255, 255, 255, 0.06);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
}

.lg-info-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.02em;
}

.lg-info-value {
  font-size: 17px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.lg-tag-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
</style>

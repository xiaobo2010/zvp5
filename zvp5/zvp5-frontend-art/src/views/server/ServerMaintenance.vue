<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import type { FormInstance, FormRules } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { errorDetailOf, resolveErrorMessage } from '@/utils/errorMessage'
import {
  getMaintenanceStatus,
  enableMaintenance,
  disableMaintenance,
  type MaintenanceStatus
} from '@/api/system/maintenance.ts'
import ZHexUnit from './components/ZHexUnit.vue'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const isAdmin = userStore.position.includes('admin')

// —— 系统维护模式卡片:仅 admin 可见,与后端严格 admin 校验一致 ——
const maintenanceStatus = ref<MaintenanceStatus | null>(null)
const statusLoading = ref(false)
const submitting = ref(false)
const maintenanceFormRef = ref<FormInstance>()
const form = reactive({ reason: '', announcement: '' })

const M = 'server.maintenance.'

const rules: FormRules = {
  reason: [{ required: true, message: t(M + 'reasonRequired'), trigger: 'blur' }],
  announcement: [{ max: 300, message: t(M + 'announcementTooLong'), trigger: 'blur' }]
}

const sinceText = computed(() => {
  const since = maintenanceStatus.value?.since
  if (!since) return ''
  return dayjs(since * 1000).format('YYYY-MM-DD HH:mm:ss')
})

/** 维护已开启 = 全平台停机,这是本页最要紧的状态,徽章放大并转危险色。 */
const maintenanceOn = computed(() => maintenanceStatus.value?.enabled === true)

const statusTone = computed<'danger' | 'ok' | 'idle'>(() => {
  if (statusLoading.value) return 'idle'
  if (!maintenanceStatus.value) return 'idle'
  return maintenanceOn.value ? 'danger' : 'ok'
})

const statusValue = computed(() => {
  if (statusLoading.value) return '···'
  if (!maintenanceStatus.value) return '—'
  return maintenanceOn.value ? 'ON' : 'OFF'
})

const statusHint = computed(() => {
  if (statusLoading.value) return t('server.hud.loading')
  if (!maintenanceStatus.value) return t(M + 'statusFailed')
  return maintenanceOn.value
    ? t(M + 'unitSince', { time: sinceText.value })
    : t(M + 'unitIdle')
})

async function refreshStatus() {
  statusLoading.value = true
  try {
    const res = await getMaintenanceStatus()
    maintenanceStatus.value = res.data.data
  } catch {
    ElMessage.error(t(M + 'statusFailed'))
  } finally {
    statusLoading.value = false
  }
}

async function onEnable() {
  // 请求在飞时直接忽略二次点击:表单校验会先跑,慢网下双击会开两次确认框。
  if (submitting.value) return
  const valid = await maintenanceFormRef.value?.validate().catch(() => false)
  if (!valid) return
  try {
    await ElMessageBox.confirm(t(M + 'enableConfirm'), t(M + 'title'), { type: 'warning' })
  } catch {
    return
  }
  submitting.value = true
  try {
    await enableMaintenance(form.reason.trim(), form.announcement.trim() || undefined)
    ElMessage.success(t(M + 'enableSuccess'))
    form.reason = ''
    form.announcement = ''
    await refreshStatus()
  } catch (err) {
    // 只有后端给了 detail 才叠一条本页 toast，避免和全局拦截器重复刷屏。
    if (errorDetailOf(err)) {
      ElMessage.error(resolveErrorMessage(err))
    }
  } finally {
    submitting.value = false
  }
}

async function onDisable() {
  if (submitting.value) return
  try {
    await ElMessageBox.confirm(t(M + 'disableConfirm'), t(M + 'title'), { type: 'warning' })
  } catch {
    return
  }
  submitting.value = true
  try {
    await disableMaintenance(form.reason.trim() || undefined)
    ElMessage.success(t(M + 'disableSuccess'))
    form.reason = ''
    form.announcement = ''
    await refreshStatus()
  } catch (err) {
    // 只有后端给了 detail 才叠一条本页 toast，避免和全局拦截器重复刷屏。
    if (errorDetailOf(err)) {
      ElMessage.error(resolveErrorMessage(err))
    }
  } finally {
    submitting.value = false
  }
}

if (isAdmin) {
  onMounted(() => {
    void refreshStatus()
  })
}
</script>

<template>
  <!-- 系统维护模式:仅 admin 可见 -->
  <div v-if="isAdmin" class="phi-srv-page">
    <!-- 状态单元:维护开关是本页唯一要紧的状态,置顶放大 -->
    <div class="phi-srv-strip" :class="{ 'phi-srv-strip--danger': maintenanceOn }">
      <ZHexUnit
        :label="t(M + 'unitLabel')"
        :value="statusValue"
        :tone="statusTone"
        size="lg"
        :hint="statusHint"
      />
    </div>

    <!-- 维护已开启:公告顶到最前,一眼看到对外说了什么 -->
    <div v-if="maintenanceOn && maintenanceStatus?.announcement" class="phi-srv-alert">
      <span class="phi-srv-tag phi-srv-tag--danger">SYS // NOTICE</span>
      <div class="phi-srv-alert__body">
        <p class="whitespace-pre-line">{{ maintenanceStatus.announcement }}</p>
      </div>
    </div>

    <!-- 操作区 -->
    <section class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-card__title">{{ t(M + 'opTitle') }}</h2>
        <span class="phi-srv-tag">{{ t(M + 'unitLabel') }}</span>
      </header>
      <div class="yg-card__body maintenance-body">
        <p class="phi-srv-dim">{{ t(M + 'opNote') }}</p>

        <template v-if="maintenanceStatus">
          <ElForm
            ref="maintenanceFormRef"
            :model="form"
            :rules="rules"
            label-position="top"
            class="mt-4"
          >
            <ElFormItem :prop="'reason'" :label="t(M + 'reasonLabel')">
              <ElInput
                v-model="form.reason"
                type="textarea"
                :rows="2"
                maxlength="200"
                show-word-limit
                :placeholder="t(M + 'reasonHint')"
              />
            </ElFormItem>
            <ElFormItem :prop="'announcement'" :label="t(M + 'announcementLabel')">
              <ElInput
                v-model="form.announcement"
                type="textarea"
                :rows="3"
                maxlength="300"
                show-word-limit
                :placeholder="t(M + 'announcementHint')"
              />
            </ElFormItem>
            <ElFormItem>
              <ElButton
                v-if="!maintenanceOn"
                type="primary"
                :loading="submitting"
                :disabled="submitting"
                @click="onEnable"
              >
                {{ t(M + 'enable') }}
              </ElButton>
              <ElButton
                v-else
                type="danger"
                plain
                :loading="submitting"
                :disabled="submitting"
                @click="onDisable"
              >
                {{ t(M + 'disable') }}
              </ElButton>
            </ElFormItem>
          </ElForm>
        </template>

        <!-- 首次加载:骨架屏占位,避免「点了没反应」 -->
        <div v-else-if="statusLoading" class="phi-srv-skel mt-4">
          <div class="phi-srv-skel__line phi-srv-skel__line--tall"></div>
          <div class="phi-srv-skel__line"></div>
          <div class="phi-srv-skel__line phi-srv-skel__line--short"></div>
        </div>

        <div v-else class="mt-4 flex flex-wrap items-center gap-3">
          <span class="phi-srv-dim">{{ t(M + 'statusFailed') }}</span>
          <ElButton text bg :loading="statusLoading" @click="refreshStatus">
            {{ t('maintenance.recheck') }}
          </ElButton>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.maintenance-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}
</style>

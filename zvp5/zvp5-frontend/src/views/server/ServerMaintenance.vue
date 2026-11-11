<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElCard } from 'element-plus/es/components/card/index.mjs'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
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
  <ElCard v-if="isAdmin" shadow="never">
    <template #header>
      <span class="font-medium">{{ t(M + 'title') }}</span>
    </template>
    <template v-if="maintenanceStatus">
      <div class="flex flex-col gap-3">
        <span class="text-base">
          {{
            maintenanceStatus.enabled
              ? t(M + 'statusMaintaining', { time: sinceText })
              : t(M + 'statusNormal')
          }}
        </span>
        <ElAlert
          v-if="maintenanceStatus.enabled && maintenanceStatus.announcement"
          type="warning"
          show-icon
          :closable="false"
          :title="maintenanceStatus.announcement"
          class="whitespace-pre-line"
        />
      </div>
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
            v-if="!maintenanceStatus.enabled"
            type="primary"
            :loading="submitting"
            @click="onEnable"
          >
            {{ t(M + 'enable') }}
          </ElButton>
          <ElButton v-else type="danger" plain :loading="submitting" @click="onDisable">
            {{ t(M + 'disable') }}
          </ElButton>
        </ElFormItem>
      </ElForm>
    </template>
    <div v-else class="flex items-center gap-3">
      <span class="text-sm text-gray-500 dark:text-gray-400">
        {{ t(M + 'statusFailed') }}
      </span>
      <ElButton text bg :loading="statusLoading" @click="refreshStatus">
        {{ t('maintenance.recheck') }}
      </ElButton>
    </div>
  </ElCard>
</template>
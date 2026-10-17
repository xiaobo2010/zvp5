<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, reactive, computed, onMounted, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElIcon, ElMessage, ElMessageBox, ElCard, ElForm, ElFormItem, ElInput, ElButton, ElAlert } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { ArrowLeft, Upload } from '@element-plus/icons-vue'
import { Group, Log, User } from '@icon-park/vue-next'
import ZGroupUserList from '@/components/group/ZGroupUserList.vue'
import ZGroupList from '@/components/group/ZGroupList.vue'
import ZLogList from '@/components/log/ZLogList.vue'
import TablerSum from '@/icons/TablerSum.vue'
import ZGroupUserTimeList from '@/components/group/ZGroupUserTimeList.vue'
import dayjs from '@/plugins/dayjs'
import { isAxiosError } from 'axios'
import {
  getMaintenanceStatus,
  enableMaintenance,
  disableMaintenance,
  type MaintenanceStatus
} from '@/api/system/maintenance.ts'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const id = ref<string>(route.params.id as string)

if (!(userStore.position.includes('admin') || userStore.position.includes('department'))) {
  router.replace('/not-found')
}

watch(
  () => route.params.id,
  (value) => {
    id.value = value as string
  }
)
const curPage = route.params.action?.toString()

const current = ref(curPage && curPage !== '' ? curPage : 'groups')

const tabs = ref<SegmentedTab[]>([
  {
    label: 'Groups',
    value: 'groups',
    icon: Group
  },
  {
    label: 'Users',
    value: 'users',
    icon: User
  },
  {
    label: 'Time',
    value: 'time',
    icon: TablerSum
  },
])

if (userStore.position.includes('admin')) {
  tabs.value[tabs.value.length] = {
    label: 'Logs',
    value: 'logs',
    icon: Log
  }
}

watch(current, () => {
  router.replace('/manage/' + current.value)
})

watch(
  () => route.params.action,
  (value) => {
    console.log(value)
    const curPage = route.params.action?.toString()
    current.value = curPage && curPage !== '' ? curPage : 'groups'
  }
)

// —— 系统维护模式卡片:仅 admin 可见,与后端严格 admin 校验一致 ——
const isAdmin = userStore.position.includes('admin')
const maintenanceStatus = ref<MaintenanceStatus | null>(null)
const statusLoading = ref(false)
const submitting = ref(false)
const maintenanceFormRef = ref<FormInstance>()
const form = reactive({ reason: '', announcement: '' })

const M = 'manage.manage.maintenance.'

const rules: FormRules = {
  reason: [{ required: true, message: t(M + 'reasonRequired'), trigger: 'blur' }],
  announcement: [{ max: 300, message: t(M + 'announcementTooLong'), trigger: 'blur' }]
}

const sinceText = computed(() => {
  const since = maintenanceStatus.value?.since
  if (!since) return ''
  return dayjs(since * 1000).format('YYYY-MM-DD HH:mm:ss')
})

// 从后端错误里提取 detail 用于展示
function detailOf(err: unknown): string {
  if (isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

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
    const detail = detailOf(err)
    if (detail) {
      ElMessage.error(detail)
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
    const detail = detailOf(err)
    if (detail) {
      ElMessage.error(detail)
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
  <div class="px-16 py-8">
    <ElPageHeader :icon="ArrowLeft" @back="() => router.back()" class="py-4">
      <template #content>
        {{ t('manage.manage.tabs.' + current) }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t(('manage.manage.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>
    <ZGroupList v-if="current === 'groups'" />
    <ZGroupUserList v-else-if="current === 'users'" id="" />
    <ZLogList v-else-if="current === 'logs'" id="" user="" />
    <ZGroupUserTimeList v-else-if="current === 'time'" id="" />
    <!-- 系统维护模式:仅 admin 可见,常驻于各 tab 内容之后 -->
    <ElCard v-if="isAdmin" shadow="never" class="mt-8">
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
  </div>
</template>

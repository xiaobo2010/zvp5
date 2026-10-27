<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElDialog, ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { readMyDevice, setMyPrimaryDevice, type MyDeviceStatus } from '@/api/user/device'
import { primaryDeviceErrorMessage, primaryDeviceGateMode } from './primaryDeviceLogic'

/**
 * 存量用户的主设备强制门。
 *
 * 后端对未绑定用户登录是 fail-open 放行（锁死等于全站不可用），强制绑定只能由
 * 前端补上：登录后读一次设备状态，未绑定就弹一个不可关闭的窗，本机是学海设备
 * 就直接绑定，否则只能退出登录换设备。
 *
 * 账号切换按 oid 隔离并丢弃旧响应，同机换人不会把上一个账号的状态带过来。
 */

const { t } = useI18n()
const router = useRouter()
const userStore = useUserStore()

const status = ref<MyDeviceStatus | null>(null)
const binding = ref(false)

const accountKey = computed(() => {
  if (!userStore.isLogin) {
    return ''
  }
  return String(userStore._id || userStore.id || '')
})

let generation = 0

function resetForAccount() {
  generation += 1
  status.value = null
  binding.value = false
}

const mode = computed(() =>
  primaryDeviceGateMode({
    isLogin: userStore.isLogin,
    roles: userStore.position,
    status: status.value
  })
)

const visible = computed(() => mode.value !== 'hidden')
const currentDevice = computed(() => status.value?.xuehai_id ?? '')

async function evaluate() {
  const key = accountKey.value
  if (!userStore.isLogin || !key) {
    return
  }

  generation += 1
  const requestGeneration = generation
  status.value = null

  // 存量用户的角色可能来自持久化快照，先刷新资料再判特权角色；刷新失败不阻塞设备检查。
  await userStore.refreshUser().catch(() => {})
  if (requestGeneration !== generation || key !== accountKey.value) {
    return
  }

  try {
    const data = await readMyDevice()
    if (requestGeneration !== generation || key !== accountKey.value) {
      return
    }
    status.value = data
  } catch {
    // 读取失败不弹门：说明不了用户没绑定，fail-open 与后端登录口径一致。
    if (requestGeneration === generation) {
      status.value = null
    }
  }
}

async function bindCurrentDevice() {
  const xuehaiId = currentDevice.value
  if (!xuehaiId || binding.value) {
    return
  }
  binding.value = true
  try {
    await setMyPrimaryDevice(xuehaiId)
    ElMessage.success(t('device.gate.bindSuccess'))
    await evaluate()
  } catch (error) {
    ElMessage.error(primaryDeviceErrorMessage(error, t('device.gate.bindFailed')))
  } finally {
    binding.value = false
  }
}

async function logout() {
  await userStore.removeUser()
  await router.replace('/user/login')
}

// 登录态翻转与账号切换都走同一条评估路径；两个值在同一 tick 变化时只触发一次，
// 避免刚登录就发出两遍 refreshUser + readMyDevice。
watch([() => userStore.isLogin, accountKey], ([loggedIn, key]) => {
  resetForAccount()
  if (loggedIn && key) {
    void evaluate()
  }
})

onMounted(() => {
  if (userStore.isLogin) {
    void evaluate()
  }
})
</script>

<template>
  <ElDialog
    :model-value="visible"
    :title="t('device.gate.title')"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :destroy-on-close="true"
    append-to-body
    width="min(440px, 92vw)"
    class="primary-device-gate"
  >
    <div class="device-gate">
      <p class="device-gate__lead">
        {{ mode === 'bindOnDevice' ? t('device.gate.bindIntro') : t('device.gate.logoutIntro') }}
      </p>

      <div v-if="mode === 'bindOnDevice'" class="device-gate__device">
        <span class="device-gate__label">{{ t('device.gate.deviceLabel') }}</span>
        <span class="device-gate__value yg-text-num">{{ currentDevice }}</span>
      </div>

      <p class="device-gate__hint">
        {{ mode === 'bindOnDevice' ? t('device.gate.bindHint') : t('device.gate.logoutHint') }}
      </p>
    </div>

    <template #footer>
      <div class="device-gate__actions">
        <ElButton
          v-if="mode === 'bindOnDevice'"
          type="primary"
          size="large"
          :loading="binding"
          @click="bindCurrentDevice"
        >
          {{ binding ? t('device.gate.binding') : t('device.gate.bind') }}
        </ElButton>
        <!-- bindOnDevice 也要有出口：在本机不合适绑定（比如借用他人的平板）时，
             强制门不能变成只能杀进程的牢笼。 -->
        <ElButton v-if="mode === 'bindOnDevice'" text size="large" @click="logout">
          {{ t('device.gate.logout') }}
        </ElButton>
        <ElButton v-else type="danger" size="large" @click="logout">
          {{ t('device.gate.logout') }}
        </ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.device-gate {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
  color: var(--yg-text-primary);
}

.device-gate__lead {
  margin: 0;
  font-size: var(--yg-font-size-md);
  line-height: var(--yg-line-height-relaxed);
}

.device-gate__device {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yg-space-4);
  padding: var(--yg-space-3) var(--yg-space-4);
  border: calc(var(--yg-space-1) / 4) solid var(--yg-border-default);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
}

.device-gate__label {
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
}

.device-gate__value {
  color: var(--yg-text-primary);
  font-size: var(--yg-font-size-xl);
  font-weight: var(--yg-font-weight-semibold);
}

.device-gate__hint {
  margin: 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
}

.device-gate__actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 560px) {
  .device-gate__actions :deep(.el-button) {
    width: 100%;
    margin-left: 0;
    min-height: var(--yg-control-height-lg);
  }
}
</style>

<style>
/* 强制门不可关闭，弹窗内部必须实色 —— 与 ZNotificationReminder 同一口径。 */
.primary-device-gate.el-dialog {
  background: var(--yg-bg-surface);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-lg);
}

.primary-device-gate .el-dialog__header {
  border-bottom: 1px solid var(--yg-border-default);
  padding-bottom: 12px;
}

.primary-device-gate .el-dialog__title {
  color: var(--yg-text-primary);
  font-size: 16px;
  font-weight: 600;
}

.primary-device-gate .el-dialog__body {
  padding-top: 16px;
}

.primary-device-gate .el-dialog__footer {
  border-top: 1px solid var(--yg-border-default);
  padding-top: 14px;
}
</style>

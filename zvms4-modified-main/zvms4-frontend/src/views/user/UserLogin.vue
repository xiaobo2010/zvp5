<script setup lang="ts">
import { ref, watch, h } from 'vue'
import {
  ElButton,
  ElCard,
  ElMessageBox,
  ElMessage,
} from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useHeaderStore } from '@/stores/header'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { modifyPasswordDialogs } from '@/views'
import api from '@/api'
import { getMaintenanceStatus } from '@/api/system/maintenance.ts'

const header = useHeaderStore()

const { t } = useI18n()

const user = ref('')
const password = ref<string>('')
const loginfield = ref()
const userStore = useUserStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)

header.setHeader(t('nav.login.actions.login'))

// 站内回跳目标,仅接受以 / 开头的 query,避免外链注入
function redirectTarget(): string {
  const target = route.query.redirect
  return typeof target === 'string' && target.startsWith('/') ? target : ''
}

// 登录后按角色分流:普通用户维护中一律回维护暂停页;redirect 参数仅在站内路径生效,缺省 /user
async function postLoginTarget(): Promise<string> {
  if (userStore.position.includes('admin')) {
    return redirectTarget() || '/user'
  }
  try {
    const res = await getMaintenanceStatus()
    if (res.data.data.enabled) {
      return '/maintenance'
    }
  } catch {
    // 状态接口不可达时按未维护处理
  }
  return redirectTarget() || '/user'
}

// 维护模式 503(v4.2.4-2 R4):维护期间后端只给 admin 下发 token,非 admin 的老
// 登录请求会被拒。该响应只做提示 —— 绝不清 token、绝不跳登录页,清 token 只属
// 于 axios 的 401 分支(plugins/axios.ts:168-186)。
function isMaintenanceBlocked(error: unknown): boolean {
  const response = (
    error as { response?: { status?: number; data?: { maintenance?: boolean } } }
  )?.response
  return response?.status === 503 && response?.data?.maintenance === true
}

function notifyMaintenanceBlocked() {
  ElMessage({
    message: t('maintenance.loginBlocked'),
    type: 'warning',
    grouping: true,
    plain: true
  })
}

if (userStore.isLogin) {
  void postLoginTarget().then((target) => router.replace(target))
} else {
  localStorage.removeItem('token')
}

async function login() {
  if (loading.value || password.value === '' || String(user.value).length !== 8) {
    return
  }
  if (!userStore.validatePasswordStrength(password.value)) {
    loading.value = true
    
    const id = user.value
    const { token, _id } = (await api.user.auth
      .useLongTermAuth(id, password.value, 'short', 'login')
      .catch((error: unknown) => {
        loading.value = false
        if (isMaintenanceBlocked(error)) {
          notifyMaintenanceBlocked()
        }
      })) || {
      token: '',
      _id: '',
    }
    if (!token) {
      loading.value = false
      return
    }
    await ElMessageBox({
      message: h(
        'p',
        null,
        [
          h('span', null, '为确保您的账号安全，'),
          h('strong', null, '您必须修改密码后才能继续使用本系统。')
        ]
      ),
      showCancelButton: true,
      title: '您必须修改密码',
      confirmButtonText: '修改密码',
      cancelButtonText: '取消',
      showClose: false,
      closeOnClickModal: false,
      closeOnPressEscape: false,
    }).then(() => reset_and_login(_id, token)).catch(() => {
      loading.value = false
    })
    return
  }
  loading.value = true
  const id = user.value
  await userStore
    .setUser(id, password.value as string)
    .then(async () => router.replace(await postLoginTarget()))
    .catch((error: unknown) => {
      loading.value = false
      if (isMaintenanceBlocked(error)) {
        notifyMaintenanceBlocked()
      }
    })
}

async function reset_and_login(id: string, token: string) {
  await modifyPasswordDialogs(id, 'zh-CN', async (a: string, new_password: string) => {
    await api.user.password.put(id, new_password, token).catch(() => {
      loading.value = false
    })
    await userStore
      .setUser(id, new_password)
      .then(async () => router.replace(await postLoginTarget()))
      .catch(() => {
        loading.value = false
      })
  }, token).catch(() => {
    loading.value = false
  })
}

watch(user, async () => {
  await loginfield.value.validate()
})
</script>

<template>
  <div class="w-full login-wrap" style="display: flex; flex-direction: column; height: 100%;">
    <div style="flex-grow: 1;"></div>
    <ElCard shadow="never" class="login-field text-center w-full" style="flex-grow: 0;">
      <p class="login-title">{{ t('nav.login.pageTitle') }}</p>
      <ElForm ref="loginfield" label-position="top">
        <ElFormItem :label="t('nav.login.form.account')" prop="id">
          <ElInput
            v-model.number="user"
            :minlength="8"
            :maxlength="8"
            clearable
            type="number"
            class="login-input w-full"
          />
        </ElFormItem>
        <ElFormItem :label="t('nav.login.form.password')" prop="password">
          <ElInput
            type="password"
            v-model="password"
            clearable
            show-password
            class="login-input w-full"
            @keyup.enter="login"
          />
        </ElFormItem>
      </ElForm>
      <div class="actions">
        <ElButton
          type="primary"
          size="large"
          class="login-button"
          @click="login"
          :icon="ArrowRight"
          :disabled="password === '' || String(user).length !== 8"
          :loading="loading"
        >
          {{ t('nav.login.actions.login') }}
        </ElButton>
      </div>
    </ElCard>
    <div style="flex-grow: 1;"></div>
    <div style="flex-grow: 0; min-height: 3.75rem; max-height: 3.75rem;"></div>
  </div>
</template>

<style scoped>
.login-field {
  width: 88%;
  max-width: 400px;
  padding: 32px 24px;
  text-align: center;
  margin-left: auto;
  margin-right: auto;
}

.login-title {
  font-size: 30px;
  line-height: 1.25;
  font-weight: 600;
  text-align: center;
  margin: 0 0 20px;
}

.login-input {
  --el-input-height: 44px;
}

.login-field :deep(.el-form-item__label) {
  font-size: 16px;
  font-weight: 500;
  letter-spacing: normal;
  color: var(--zv-text, #303133);
}

.login-field :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-button {
  width: 100%;
  font-size: 16px;
  height: 46px;
}
</style>

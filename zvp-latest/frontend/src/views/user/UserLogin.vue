<script setup>
import { ref, watch, h } from 'vue'
import {
  ElMessageBox,
  ElMessage,
  ElButton,
  ElInput
} from 'element-plus'
import { InfoFilled, ArrowRight } from '@element-plus/icons-vue'
import { useUserStore, useState } from '@/stores'
import { useRouter } from 'vue-router'

const userStore = useUserStore()
const state = useState()

if (userStore.isLogin) {
  useRouter().replace('/user')
} else {
  localStorage.removeItem('token')
}

const user = ref('')
const password = ref('')
const router = useRouter()
const loading = ref(false)

async function login() {
    try{
  if (loading.value || password.value === '') {
    return
  }
  loading.value = true
  const id = user.value
  await userStore
    .setUser(id, password.value, true)
    .then(() => router.replace('/user'))
    .catch(ex => {
      loading.value = false
      throw ex
    })
    } catch(ex) {
        loading.value = false
        throw ex
    }
}

async function reset_and_login(id, token) {
  await (await import('@/dialogs')).modifyPasswordDialogs(id, 'zh-CN', async (_, new_password) => {
    await (await import('@/api')).user.password.put_v2(id, new_password, token).catch(ex => {
      loading.value = false
      throw ex
    })
    await userStore
      .setUser(id, new_password, true)
      .then(() => router.replace('/user'))
      .catch(() => {
        loading.value = false
      })
  }, token).catch((ex) => {
    // alert(ex?.stack??ex)
    loading.value = false
  })
}

async function featureTest() {
}
</script>

<template>
    <div class="login-background">
        <div class="login-card" v-loading="loading || userStore.isLogin">
            <span class="login-title" @dblclick="featureTest()">登录</span>
            <!--不如把前两个X改成20，不然还有人填考号-->
            <ElInput v-model="user" placeholder="XXXXXXXX">
                <template #prefix>学号：</template>
            </ElInput>
            <ElInput v-model="password" type="password">
                <template #prefix>密码：</template>
            </ElInput>
            <div class="login-controls flex">
                <ElButton text bg type="info" :icon="InfoFilled" @click="$router.push('/about')">关于</ElButton>
                <div style="flex-grow: 1;"></div>
                <ElButton text bg type="primary" :icon="ArrowRight" :disabled="!password" @click="login">登录</ElButton>
            </div>
        </div>
    </div>
</template>

<style scoped>
.actions {
  text-align: right;
}

.login-background {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    background: var(--el-bg-color-page);
    overflow-y: scroll;
    overflow-x: hidden;
}

.login-card {
    background: var(--el-bg-color);
    box-shadow: var(--el-box-shadow);
    border-radius: 1rem;
    margin: 2rem;
    width: calc(100% - 4rem);
    max-width: 800px;
    display: flex;
    flex-direction: column;
    gap: .75rem;
    flex-shrink: 0;
    padding: 1rem;
    overflow: hidden;
}

.login-title {
    align-self: center;
    font-size: xx-large;
    font-weight: bold;
    flex-shrink: 0;
    padding: 1rem;
}

.login-controls {
    flex-shrink: 0;
}
</style>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElInputOtp } from 'element-plus'
import 'element-plus/theme-chalk/el-input-otp.css'
import axios from '@/plugins/axios'

const loading = ref(true)
const otp = ref('')
const exp = ref(0)

onMounted(() => {
    let lastid = 0
    const interval = setInterval(async () => {
        if (!navigator.onLine) {
            loading.value = true
            otp.value = ''
            exp.value = 0
            return
        }
        const id = new Date().getTime()
        lastid = id
        const resp = await axios('/v1/platform/totp', {
            method: 'get',
            nprogress: false,
        }).catch(ex => {
            if(lastid !== id) throw ex
            loading.value = true
            otp.value = ''
            exp.value = 0
            throw ex
        })
        if (lastid !== id) {
            // 顺序不对
        } else if (resp.status != 'error' && resp.data?.code) {
            loading.value = false
            const { code, exp: expire } = resp.data
            otp.value = '*' + code + '*'
            exp.value = expire
        } else {
            loading.value = true
            otp.value = ''
            exp.value = 0
        }
    }, 1000)
    onUnmounted(() => {
        clearInterval(interval)
    })
})

</script>

<template>
    <div style="display: flex; flex-direction: row; justify-content: center; flex-shrink: 0; overflow-x: scroll; align-items: center; gap: 1rem; padding-bottom: .5rem" v-loading="loading">
        <ElInputOtp :length="8" type="underlined" size="large" readonly v-model="otp" :disabled="!otp">
        </ElInputOtp>
        <span :style="`color: var(--el-${exp > 5 || exp == 0 ? 'text-color-secondary' : 'color-danger'}); font-family: 'Ubuntu Mono', monospace, Ubuntu`">
            {{ String(exp||'--').padStart(2,0) }}s
        </span>
    </div>
</template>
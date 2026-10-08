<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useState } from '@/stores'
import { ElPageHeader, ElResult } from 'element-plus'
import 'element-plus/theme-chalk/el-result.css'

const router = useRouter()
const state = useState()
</script>

<template>
    <div class="notfound-panel flex flex-col">
        <ElPageHeader class="flex-shrink-0" @back="$router.back()">
            <template #content>
                页面不存在
            </template>
        </ElPageHeader>
        <div class="notfound flex flex-col">
            <ElResult
                status="404"
                icon="error"
                title="404 页面不存在"
                sub-title="你是怎么点进来的 T_T"
            >
            </ElResult>
        </div>
    </div>
</template>

<style scoped>
.notfound-panel {
    padding: 2rem;
    background: var(--el-bg-color);
}

.notfound {
    justify-content: center;
    align-items: center;
    flex-grow: 1;
}
</style>

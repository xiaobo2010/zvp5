<script setup lang="ts">
import { useState } from '@/stores'
import { ElPageHeader, ElButton } from 'element-plus'

const state = useState()

const dev = () => {
    location.href='/dev'//prompt()??(()=>{throw null})()
}
</script>

<template>
    <div class="about-panel">
        <ElPageHeader class="about-panel-header" @back="$router.back()">
            <template #content>
                关于
            </template>
        </ElPageHeader>
        <img class="app-logo" src="/favicon.jpg" alt="logo" />
        <span class="app-name">义工平台</span>
        <span class="app-version" @dblclick="dev">v4.2.0.20261005-beta</span>
        <span>&nbsp;</span>
        <span class="app-copyright">&copy; 2018-2026 镇海中学学生会 | MIT Licensed</span>
    </div>
</template>

<style scoped>
.about-panel {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 1rem;
    padding: 2rem;
    background: var(--el-bg-color);
}
.about-panel * {
    flex-shrink: 0;
}
.app-logo {
    flex-shrink: 1;
    width: 128px;
    height: 128px;
    border-radius: 1rem;
    box-shadow: var(--el-box-shadow);
}
.app-name {
    font-size: larger;
}
.app-version {
    color: var(--el-text-color-secondary);
}
.app-copyright {
    position: absolute;
    bottom: 2rem;
    left: 0;
    right: 0;
    text-align: center;
    color: var(--el-text-color-placeholder);
}
.about-panel-header {
    position: absolute;
    top: 2rem;
    left: 2rem;
    right: 2rem;
}
</style>

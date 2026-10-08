<script setup>
import { useUserStore } from '@/stores'
import { useRouter } from 'vue-router'
import {
    ElMessageBox,
    ElPageHeader,
    ElAvatar,
    ElTag,
    ElButton,
    ElSwitch
} from 'element-plus'
import { useDark } from '@vueuse/core'

const user = useUserStore()
const router = useRouter()
const dark = useDark()

const positions = {
    secretary: '团支书',
    department: '学生会',
    admin: '管理员',
    teacher: '教师', // reserved
}

const lastTwoChars = (s = '') => s.substring(Math.max(s.length - 2, 0))

const modifyPassword = async () => {
    await (await import('@/dialogs')).modifyPasswordDialogs(user._id, 'zh-CN', user.resetPassword)
}

const logout = async () => {
    await ElMessageBox({
        title: '提示',
        message: '确定要退出登录吗？',
        showCancelButton: true,
        cancelButtonText: '不，点错了',
        confirmButtonText: '仍然登出',
        showClose: false,
        closeOnClickModal: false,
        closeOnPressEscape: false,
        confirmButtonClass: 'el-button--danger',
        type: 'warning',
    })
    await user.removeUser()
    router.replace('/user/login')
}
</script>

<template>
    <div class="profile-panel">
        <!--这个可以删掉，很丑-->
        <ElPageHeader class="profile-panel-header flex-shrink-0 noback" @back="$router.back()">
            <template #content>
                我的
            </template>
        </ElPageHeader>
        <div class="user-info-card">
            <ElAvatar class="user-avatar" :size="80">{{ lastTwoChars(user.name) }}</ElAvatar>
            <div class="user-info">
                <div class="user-info-inner">
                    <span class="user-name">
                        {{ user.name }}
                    </span>
                    <span class="user-roles">
                        <ElTag
                            v-for="role in user.position.filter(x => x !== 'student')"
                            disable-transitions
                            effect="plain"
                        >
                            {{ positions[role] ?? role }}
                        </ElTag>
                    </span>
                </div>
                <span class="user-class">
                    {{ user.class_name || user.class_id }}
                </span>
            </div>
        </div>
        <div class="user-actions-card">
            <ElButton text size="large" class="action" @click="modifyPassword()">修改密码</ElButton>
            <ElButton text size="large" class="action" @click="dark=!dark">
                深色模式
                <div class="action-content">
                <ElSwitch v-model="dark" @click="dark=!dark"></ElSwitch>
                </div>
            </ElButton>
            <ElButton text size="large" class="action" @click="$router.push('/about')">关于</ElButton>
        </div>
        <div class="user-actions-card">
            <ElButton text size="large" class="action" style="justify-content: center;" type="danger" @click="logout()">退出登录</ElButton>
        </div>
    </div>
</template>

<style scoped>
.profile-panel {
    display: flex;
    flex-direction: column;
    padding: 2rem;
    background: var(--el-bg-color);
    gap: 2rem;
}

.user-info-card, .user-actions-card {
    border-radius: 1rem;
    box-shadow: var(--el-box-shadow-light);
    flex-shrink: 0;
}

.user-info-card, .user-info-inner, .user-roles {
    display: flex;
    flex-direction: row;
    gap: .5rem;
}

.user-info-card {
    padding: 2rem;
    gap: 1rem;
}

.user-actions-card, .user-info {
    display: flex;
    flex-direction: column;
}

.user-info {
    gap: .5rem;
}

.user-class {
    color: var(--el-text-color-secondary);
}

.user-info-inner {
    align-items: center;
}

.user-actions-card {
    overflow: hidden;
}

.user-avatar {
    flex-shrink: 0;
    font-size: x-large;
}

.user-actions-card .action {
    justify-content: flex-start;
    margin: 0;
    border-radius: 0;
    font-size: large;
    padding: 1.5rem;
    display: flex;
    flex-shrink: 0;
    position: relative;
}

.action-content {
    display: flex;
    flex-direction: row;
    align-items: center;
    overflow: visible;
    top: 1.5rem;
    right: 1.5rem;
    bottom: 1.5rem;
    position: absolute;
}

.user-actions-card .action + .action {
    border-top: var(--el-border);
}

.user-name {
    font-size: x-large;
}
</style>
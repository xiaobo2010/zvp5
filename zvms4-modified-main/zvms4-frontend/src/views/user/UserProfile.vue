<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useDark } from '@vueuse/core'
import {
  ElAvatar,
  ElButton,
  ElCard,
  ElDescriptions,
  ElDescriptionsItem,
  ElMessageBox,
  ElPageHeader,
  ElSwitch,
  ElTag
} from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useGroupsStore } from '@/stores/groups'
import { useHeaderStore } from '@/stores/header'
import { modifyPasswordDialogs } from '@/views/user'

const user = useUserStore()
const groupsStore = useGroupsStore()
const header = useHeaderStore()
const router = useRouter()
const { t } = useI18n()
const dark = useDark()

header.setHeader(t('nav.profile'))

const className = ref('')

if (!user.isLogin) {
  router.replace('/user/login')
}

const initials = computed(() => user.name.slice(Math.max(user.name.length - 2, 0)) || '—')

const visibleRoles = computed(() => {
  const roles = user.position.filter((role) => role !== 'student')
  return roles.length > 0 ? roles : (['student'] as typeof user.position)
})

function roleLabel(role: string): string {
  return t(`manage.personalPanel.roles.${role}`)
}

async function changePassword() {
  await modifyPasswordDialogs(String(user._id), 'zh-CN', user.resetPassword)
}

async function logout() {
  try {
    await ElMessageBox.confirm(
      t('profile.logoutConfirm'),
      t('profile.logoutConfirmTitle'),
      {
        confirmButtonText: t('profile.logoutConfirmOk'),
        cancelButtonText: t('profile.logoutConfirmCancel'),
        type: 'warning',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  await user.removeUser()
  await router.replace('/user/login')
}

onMounted(async () => {
  if (!user.class_id) {
    return
  }
  const group = await groupsStore.fetchGroup(user.class_id).catch(() => undefined)
  className.value = group?.name ?? ''
})
</script>

<template>
  <div class="profile-page">
    <ElPageHeader :icon="ArrowLeft" class="profile-header" @back="router.back()">
      <template #content>{{ t('profile.pageTitle') }}</template>
    </ElPageHeader>

    <ElCard shadow="never" class="profile-card">
      <div class="profile-hero">
        <ElAvatar :size="80" class="profile-avatar">{{ initials }}</ElAvatar>
        <div class="profile-identity">
          <div class="profile-name-line">
            <span class="profile-name">{{ user.name }}</span>
            <ElTag
              v-for="role in visibleRoles"
              :key="role"
              size="small"
              effect="plain"
              class="profile-role"
            >
              {{ roleLabel(role) }}
            </ElTag>
          </div>
          <span class="profile-class">
            {{ className || user.class_id || t('profile.noClass') }}
          </span>
        </div>
      </div>

      <ElDescriptions :column="1" border class="profile-details">
        <ElDescriptionsItem :label="t('profile.labels.name')">
          {{ user.name }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.number')">
          {{ user.id }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.class')">
          {{ className || user.class_id || t('profile.noClass') }}
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.identity')">
          <ElTag
            v-for="role in visibleRoles"
            :key="role"
            size="small"
            effect="plain"
            class="profile-role"
          >
            {{ roleLabel(role) }}
          </ElTag>
        </ElDescriptionsItem>
        <ElDescriptionsItem :label="t('profile.labels.status')">
          <span :class="user.banned ? 'profile-banned' : 'profile-normal'">
            {{ user.banned ? t('profile.status.banned') : t('profile.status.normal') }}
          </span>
        </ElDescriptionsItem>
      </ElDescriptions>
    </ElCard>

    <ElCard shadow="never" class="profile-card profile-actions">
      <ElButton text size="large" class="profile-action" @click="changePassword">
        {{ t('profile.actions.changePassword') }}
      </ElButton>
      <div class="profile-action profile-switch">
        <span>{{ t('profile.actions.darkMode') }}</span>
        <ElSwitch v-model="dark" />
      </div>
      <ElButton text size="large" class="profile-action" @click="router.push('/about')">
        {{ t('profile.actions.about') }}
      </ElButton>
      <ElButton text size="large" class="profile-action" @click="router.push('/changelog')">
        {{ t('profile.actions.changelog') }}
      </ElButton>
      <ElButton
        text
        size="large"
        type="danger"
        class="profile-action profile-logout"
        @click="logout"
      >
        {{ t('profile.actions.logout') }}
      </ElButton>
    </ElCard>
  </div>
</template>

<style scoped>
.profile-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  padding: 16px 24px 32px;
}

.profile-header {
  padding: 4px 0;
}

.profile-card {
  border-radius: 8px;
}

.profile-hero {
  display: flex;
  align-items: center;
  gap: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--yg-border-default);
}

.profile-avatar {
  flex-shrink: 0;
  font-size: 24px;
  background: var(--yg-color-primary);
  color: #fff;
}

.profile-identity {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.profile-name-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.profile-name {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--yg-text-primary);
}

.profile-role {
  margin: 0;
}

.profile-class {
  color: var(--yg-text-secondary);
  font-size: 14px;
}

.profile-details {
  margin-top: 16px;
}

.profile-actions {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.profile-action {
  justify-content: flex-start;
  width: 100%;
  height: 52px;
  margin: 0;
  padding: 0 16px;
  border-radius: 0;
  font-size: 15px;
}

.profile-action + .profile-action {
  border-top: 1px solid var(--yg-border-default);
}

.profile-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--yg-text-primary);
}

.profile-logout {
  justify-content: center;
}

.profile-normal {
  color: var(--el-color-success);
}

.profile-banned {
  color: var(--el-color-danger);
}

@media (max-width: 480px) {
  .profile-page {
    padding: 12px 16px 24px;
  }

  .profile-hero {
    align-items: flex-start;
    gap: 14px;
  }

  .profile-avatar {
    width: 64px !important;
    height: 64px !important;
    font-size: 20px;
  }

  .profile-name {
    font-size: 20px;
  }
}
</style>

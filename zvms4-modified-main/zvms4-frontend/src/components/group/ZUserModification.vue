<script lang="ts" setup>
import {
  ElCard,
  ElInput,
  ElLoading,
  ElForm,
  ElFormItem,
  ElSelect,
  ElOption,
  ElButton,
  ElCheckbox,
  ElCheckboxGroup,
  ElDialog,
  ElNotification
} from 'element-plus'
import { ref, onMounted, toRefs, watch } from 'vue'
import api from '@/api'
import type { Group, User } from '@/../types'
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight } from '@element-plus/icons-vue'
import { useClipboard } from '@vueuse/core'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    id: string
    mode: 'create' | 'modify'
    cid: string
  }>(),
  {
    id: '',
    mode: 'create',
    cid: ''
  }
)

const { id, mode, cid } = toRefs(props)
const userGroups = ref<string[]>([])
const classGroupID = ref<string>('')
const permissionsID = ref<string[]>([])
const classes = ref<Group[]>([])
const loadingClasses = ref(false)
const permissions = ref<Group[]>([])
const loadingPermissions = ref(false)
const person = ref<User>()
const submission = ref(false)
const modification = reactive<User>({
  _id: id.value,
  id: '' as unknown as number,
  name: '',
  group: [],
  sex: 'unknown',
  past: []
})

if (cid.value.length === 24) {
  classGroupID.value = cid.value
}

const originalClassID = ref('')

// 创建成功后的一次性初始密码：明文只在此响应中出现，关闭弹窗后无法再查看
const createdPasswordVisible = ref(false)
const createdInitialPassword = ref('')
const { copy: copyToClipboard, copied: passwordCopied } = useClipboard({
  copiedDuring: 2000
})

async function copyInitialPassword() {
  if (!createdInitialPassword.value) return
  await copyToClipboard(createdInitialPassword.value).catch(() => {})
}

function refresh() {
  const loading = ElLoading.service({ fullscreen: true })
  if (id.value && mode.value === 'modify')
    api.user
      .readOne_v3(id.value)
      .then((res) => {
        person.value = res as User
        if (res) {
          userGroups.value = res.group ?? []
          modification.name = res.name
          modification.id = Number(res.id)
          classGroupID.value = res.class_id || ''
          originalClassID.value = res.class_id || ''
        }
        loading.close()
      })
      .catch(() => {
        loading.close()
      })
  else loading.close()
}

async function refreshClasses() {
  loadingClasses.value = true
  const classesSet = await api.group.read_v2(1, 1000, '')
  if (classesSet) {
    classes.value = classesSet.groups.filter((g) => g.type !== 'permission')
  }
  loadingClasses.value = false
}

async function refreshPermissions() {
  loadingPermissions.value = true
  const permissionsSet = await api.group.read('permission', -1)
  if (permissionsSet) {
    permissions.value = permissionsSet.groups.filter((group) => group.type === 'permission')
  }
  loadingPermissions.value = false
}

onMounted(refresh)
onMounted(refreshClasses)
onMounted(refreshPermissions)

watch(id, refresh)

async function submit() {
  submission.value = true
  if (modification.name === '') {
    return
  }
  if (classGroupID.value === '') {
    return
  }
  try {
    if (mode.value === 'create') {
      const created = await api.user.create_v3(
        modification.id.toString(),
        modification.name,
        classGroupID.value
      )
      if (created && created.initial_password) {
        // 新契约：展示一次性初始密码，弹窗即成功反馈，不再额外通知
        createdInitialPassword.value = created.initial_password
        createdPasswordVisible.value = true
        submission.value = false
        return
      }
    } else {
      if (classGroupID.value !== originalClassID.value) {
        if (!classGroupID.value && originalClassID.value) {
          await api.group.removeGroupUserV3(originalClassID.value, id.value)
        } else {
          await api.group.addGroupUserV3(classGroupID.value, id.value)
        }
        originalClassID.value = classGroupID.value
      }
    }
  } catch (_) {
    submission.value = false
    return
  }
  // 兼容未返回一次性密码的旧契约：维持原有成功通知
  ElNotification({
    title: `用户${mode.value === 'create' ? '创建成功' : '已修改'}`,
    type: 'success'
  })
  submission.value = false
}

async function remoteFetchClass(search: string) {
  if (search) {
    const classesSet = await api.group.read_v2(1, 50, search)
    if (classesSet) {
      classes.value = classesSet.groups.filter((g) => g.type !== 'permission')
    } else {
      classes.value = []
    }
  } else if (classGroupID.value) {
    const group = await api.group.readOne(classGroupID.value)
    if (group) {
      classes.value = [group]
    } else {
      classes.value = []
    }
  } else {
    classes.value = []
  }
}
</script>

<template>
  <div>
    <ElCard shadow="hover">
      <ElForm :model="modification" label-position="right" label-width="96px">
        <ElFormItem :label="t('manage.groupDetails.userList.columns.name')" required>
          <ElInput v-model="modification.name" :disabled="mode === 'modify'" />
        </ElFormItem>
        <ElFormItem :label="t('manage.groupDetails.userList.columns.id')" required>
          <ElInput v-model.number="modification.id" />
        </ElFormItem>
        <ElFormItem v-if="mode === 'modify'" :label="t('manage.groupDetails.userList.columns.classid')" required>
          <ElSelect
            v-model="classGroupID"
            remote
            :remote-method="remoteFetchClass"
            filterable
          >
            <ElOption
              v-for="group in classes"
              :key="group._id"
              :label="group.name"
              :value="group._id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem
          v-if="mode === 'modify'"
          :label="t('manage.groupDetails.userList.columns.permission')"
          required
        >
          <ElCheckboxGroup v-model="permissionsID">
            <ElCheckbox v-for="group in permissions" :key="group._id" :label="group._id" border>
              {{ group.name }}
            </ElCheckbox>
          </ElCheckboxGroup>
        </ElFormItem>
        <div style="text-align: right">
          <ElButton
            class="px-4"
            :icon="ArrowRight"
            type="primary"
            @click="submit"
            text
            bg
            :loading="submission"
            >{{ t('manage.groupDetails.userList.columns.submit') }}</ElButton
          >
        </div>
      </ElForm>
    </ElCard>
    <!-- 一次性初始密码弹窗：明文只显示这一次，必须点「我已记下」关闭 -->
    <ElDialog
      v-model="createdPasswordVisible"
      :title="t('manage.personalPanel.userCreatedTitle')"
      width="420px"
      align-center
      :close-on-click-modal="false"
    >
      <p class="text-sm leading-6 opacity-80">
        {{ t('manage.personalPanel.initialPasswordDialog.notice') }}
      </p>
      <div class="mt-4 flex items-center gap-2">
        <code
          class="min-w-0 flex-1 select-all break-all rounded bg-gray-100 px-3 py-2 font-mono text-base font-bold tracking-wider dark:bg-gray-800"
          >{{ createdInitialPassword }}</code
        >
        <ElButton
          class="flex-none"
          :type="passwordCopied ? 'success' : 'primary'"
          @click="copyInitialPassword"
        >
          {{
            passwordCopied
              ? t('manage.personalPanel.initialPasswordDialog.copied')
              : t('manage.personalPanel.initialPasswordDialog.copy')
          }}
        </ElButton>
      </div>
      <template #footer>
        <ElButton
          type="primary"
          @click="createdPasswordVisible = false"
        >
          {{ t('manage.personalPanel.initialPasswordDialog.acknowledge') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

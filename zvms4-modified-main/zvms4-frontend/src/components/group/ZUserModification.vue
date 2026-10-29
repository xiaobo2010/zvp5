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
  ElMessage,
  ElNotification
} from 'element-plus'
import { ref, onMounted, toRefs, watch } from 'vue'
import api from '@/api'
import type { Group, User, UserPosition } from '@/../types'
import { useUserStore } from '@/stores/user.ts'
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
const userStore = useUserStore()
const userGroups = ref<string[]>([])
const classGroupID = ref<string>('')
const permissionsID = ref<UserPosition[]>([])
const classes = ref<Group[]>([])
const loadingClasses = ref(false)
// 可编辑身份白名单：学生身份靠「roles 为空」表达，不出现在设置页。
// 若历史数据里带 student 等未列出角色，会落到 extraRoles
// 分支并在提交时原样保留，不会被本次改动清掉。
const allowedRoles: UserPosition[] = ['secretary', 'department', 'admin']
// UI 未列出的既有角色（如 system/auditor）：提交时原样保留，避免被意外清除
const extraRoles = ref<UserPosition[]>([])
const originalRoles = ref('')
const originalStudentId = ref('')
const person = ref<User>()
const submission = ref(false)

/**
 * 学号按字符串承载。
 *
 * 后端口径是「非空、≤32 字符、不限数字」（types/user.d.ts 的 `id: number` 是历史形态）：
 * number 装不下 >16 位的学号，也会把 `cs2019A` 这类非数字学号挡在输入阶段，
 * 所以草稿对象的 id 单独放宽成 string，提交时原样下发。
 */
interface UserDraft extends Omit<User, 'id'> {
  id: string
}

const modification = reactive<UserDraft>({
  _id: id.value,
  id: '',
  name: '',
  group: [],
  sex: 'unknown',
  past: []
})

if (cid.value.length === 24) {
  classGroupID.value = cid.value
}

const originalClassID = ref('')

// 建号后的初始密码（=学号）：由创建响应返回，弹窗当场转交，关闭后不再展示
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
          // 学号原样保留成字符串：数字形态会被 Number 截断或转成科学计数法
          modification.id = String(res.id ?? '')
          classGroupID.value = res.class_id || ''
          originalClassID.value = res.class_id || ''
          originalStudentId.value = String(res.id ?? '')
          const rawRoles = (res.roles ?? []) as UserPosition[]
          permissionsID.value = rawRoles.filter((role) => allowedRoles.includes(role))
          extraRoles.value = rawRoles.filter((role) => !allowedRoles.includes(role))
          originalRoles.value = [...rawRoles].sort().join(',')
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
  try {
    // page=-1 is the documented "all rows" mode of the v1 list endpoint.
    // `/v2/groups` caps size at 100 and must not be used as a bulk loader.
    const classesSet = await api.group.read('class', -1)
    classes.value = classesSet
      ? classesSet.groups.filter((g) => g.type !== 'permission')
      : []
  } finally {
    loadingClasses.value = false
  }
}

onMounted(refresh)
onMounted(refreshClasses)

watch(id, refresh)

async function submit() {
  submission.value = true
  if (modification.name === '') {
    submission.value = false
    return
  }
  if (classGroupID.value === '') {
    submission.value = false
    return
  }
  try {
    if (mode.value === 'create') {
      // 学号原样进请求体（字符串，不数字化）：create_v3 的第一参就是学号
      const created = await api.user.create_v3(
        modification.id,
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
      // 零改动不提交、不提示"已修改"（v4.2.4-2 R3）
      let changed = false
      const nextStudentId = modification.id.trim()
      if (nextStudentId !== originalStudentId.value) {
        // 后端口径：非空、不超过 32 字符、不含空白字符（不再限定 8 位数字）。
        if (!nextStudentId || nextStudentId.length > 32 || /\s/.test(nextStudentId)) {
          ElMessage({
            message: t('manage.personalPanel.studentIdInvalid'),
            type: 'warning',
            plain: true,
            grouping: true
          })
          submission.value = false
          return
        }
        await api.user.updateStudentId(id.value, nextStudentId)
        originalStudentId.value = nextStudentId
        changed = true
      }
      const nextRoles = [...extraRoles.value, ...permissionsID.value]
      const rolesKey = [...nextRoles].sort().join(',')
      if (rolesKey !== originalRoles.value) {
        await api.user.updateRoles(id.value, nextRoles)
        originalRoles.value = rolesKey
        changed = true
        // set_roles 会轮换 session_id：目标用户旧 token 立即失效
        if (userStore._id === id.value) {
          await userStore.refreshUser().catch(() => {})
        }
        ElNotification({
          title: t('manage.personalPanel.reloginNotice'),
          type: 'warning'
        })
      }
      if (classGroupID.value !== originalClassID.value) {
        if (!classGroupID.value && originalClassID.value) {
          await api.group.removeGroupUserV3(originalClassID.value, id.value)
        } else {
          await api.group.addGroupUserV3(classGroupID.value, id.value)
        }
        originalClassID.value = classGroupID.value
        changed = true
      }
      if (!changed) {
        submission.value = false
        return
      }
    }
  } catch (_) {
    // 失败原因由 axios 全局拦截器按响应 detail 展示，这里只中止流程、不弹成功
    submission.value = false
    return
  }
  // 兼容未返回一次性密码的旧契约：维持原有成功通知
  ElNotification({
    title: t(
      mode.value === 'create'
        ? 'manage.personalPanel.createdNotification'
        : 'manage.personalPanel.modifiedNotification'
    ),
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
          <ElInput v-model="modification.id" />
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
          :label="t('manage.personalPanel.roles.title')"
          required
        >
          <ElCheckboxGroup v-model="permissionsID">
            <ElCheckbox v-for="role in allowedRoles" :key="role" :label="role" border>
              {{ t(('manage.personalPanel.roles.' + role) as string) }}
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
    <!-- 建号初始密码弹窗：初始密码为该生学号，必须点「我已记下」关闭 -->
    <ElDialog
      v-model="createdPasswordVisible"
      :title="t('manage.personalPanel.userCreatedTitle')"
      width="420px"
      align-center
      :close-on-click-modal="false"
    >
      <p class="text-sm leading-6 opacity-80">
        {{ t('manage.personalPanel.initialPasswordDialog.studentNumberNotice') }}
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

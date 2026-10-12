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
  ElNotification
} from 'element-plus'
import { ref, onMounted, toRefs, watch } from 'vue'
import api from '@/api'
import type { Group, User } from '@/../types'
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight } from '@element-plus/icons-vue'

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
      await api.user.create_v3(
        modification.id.toString(),
        modification.name,
        classGroupID.value
      )
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
  </div>
</template>

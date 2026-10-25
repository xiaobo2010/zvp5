<script setup lang="ts">
import { useRouter } from 'vue-router'
import {
  ElFormItem,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElCard,
  ElInput,
  ElButton,
  ElCol,
  ElRow,
  ElPopover,
  ElIcon,
  ElTooltip
} from 'element-plus'
import { computed, nextTick, ref, onMounted, toRefs, watch } from 'vue'
import type { TableInstance } from 'element-plus'
import api from '@/api'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { useGroupsStore } from '@/stores/groups'
import type { User, Group } from '@/../types'
import { ZUserGroup } from '@/components'
import { useWindowSize } from '@vueuse/core'
import { Box, Search, View } from '@element-plus/icons-vue'
import { pad } from '@/plugins/ua.ts'
import ZDataExport from '@/components/utils/ZDataExport.vue'

const router = useRouter()
const { width, height } = useWindowSize()
const { t, locale } = useI18n()
const userStore = useUserStore()
const groupsStore = useGroupsStore()

const props = withDefaults(
  defineProps<{
    id: string
    selectable?: boolean
    selectorCallback?: Function
    modelValue?: User[]
  }>(),
  {
    id: '',
    selectable: false
  }
)
const emits = defineEmits(['update:modelValue'])
const group = ref<Group>()
const users = ref<User[]>([])
const page = ref(1)
const perpage = ref(10)
const loading = ref(false)
const search = ref('')
const total = ref(0)

const { id, selectable, modelValue, selectorCallback } = toRefs(props)

const tableRef = ref<TableInstance>()

/** 窄屏（≤768px，准则 3.7）：操作列收起成图标按钮，搜索/导出移到表格上方 */
const narrow = computed(() => width.value <= 768)

/** 表头只放搜索框（不排导出按钮）的场景：平板 / 非班级群组 / 可选择模式 */
const searchOnly = computed(
  () => pad() || (group.value?.type !== 'class' && id.value) || selectable.value
)

/** 分页行 + 卡片下边距 + 页面底部留白 */
const BOTTOM_ROOM = 96

/**
 * 表格高度 = 视口高度 − 表格实际顶部 − 底部预留，
 * 以表头真实位置为基准，不受上方工具栏行数影响，尽量少留白。
 */
function computeTableHeight() {
  const el = (tableRef.value as { $el?: HTMLElement } | undefined)?.$el
  const top = el?.getBoundingClientRect?.().top ?? 0
  if (top > 0 && top < height.value * 0.8) {
    return Math.max(280, Math.round(height.value - top - BOTTOM_ROOM))
  }
  const reserved = width.value < 768 ? 340 : 320
  return Math.max(280, height.value - reserved)
}

const tableHeight = ref(computeTableHeight())

watch([width, height], () => {
  tableHeight.value = computeTableHeight()
})

/** 窄屏工具栏出现/消失会改变表格顶部位置，等布局落定后重算高度 */
watch(narrow, () => {
  void nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

onMounted(() => {
  nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

onMounted(() => {
  if (id.value) {
    groupsStore.fetchGroup(id.value).then((res) => {
      group.value = res
    })
  }
})

watch(id, () => {
  if (id.value) {
    groupsStore.fetchGroup(id.value).then((res) => {
      group.value = res
    })
  }
})

const refresh = () => {
  loading.value = true
  if (id.value) {
    api.group.reads
      .users_v3(id.value, page.value, perpage.value, search.value, pwdm.value)
      .then((res) => {
        users.value = []
        users.value.push(...res.users)
        total.value = res.size
        loading.value = false
      }).catch(() => {
        loading.value = false
      })
  } else {
    api.user.read_v2(page.value, perpage.value, search.value).then((res) => {
      if (res) {
        users.value = []
        users.value.push(...res.users)
        total.value = res.size
      }
      loading.value = false
    }).catch(() => {
      loading.value = false
    })
  }
}
onMounted(refresh)
watch(id, refresh)
watch(page, refresh)
watch(perpage, () => {
  page.value = 1
  refresh()
})

function handleSearch() {
  page.value = 1
  refresh()
}

const pwdm = ref(false)
watch(pwdm, refresh)

async function exportUserList() {
  const name =
    id.value === ''
      ? locale.value === 'zh-CN'
        ? `全校学生名单`
        : `Student List`
      : locale.value === 'zh-CN'
        ? `${group?.value?.name}学生名单`
        : `Student List of ${group?.value?.name}`
  if (id.value) {
    await api.group.template(id.value, name)
  }
}

function handleSelectionChange(val: string[]) {
  emits('update:modelValue', val)
}
</script>

<template>
  <div
    v-if="
      userStore.position.includes('admin') ||
      userStore.position.includes('department') ||
      userStore.relatedGroup(id) ||
      selectable
    "
  >
    <ElCard shadow="never">
      <ElFormItem
        v-if="id && !selectable"
        :label="t('manage.groupDetails.userList.checkPasswordPrompt')"
        class="mb-4"
      >
        <ElSwitch v-model="pwdm" />
      </ElFormItem>

      <!-- 窄屏：搜索/导出移到表格上方，避免被固定操作列挤出可视区 -->
      <div v-if="narrow" class="group-user-toolbar">
        <ElInput
          v-model="search"
          size="small"
          clearable
          :placeholder="t('manage.groupList.columns.search')"
          :prefix-icon="Search"
          @blur="handleSearch"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <ElButton
          v-if="!searchOnly && id"
          :icon="Box"
          size="small"
          text
          bf
          round
          type="warning"
          @click="exportUserList"
        >
          {{ t('manage.exports.actions.export') }}
        </ElButton>
        <ElPopover v-else-if="!searchOnly" :width="320">
          <template #reference>
            <ElButton
              :icon="Box"
              size="small"
              text
              bf
              round
              type="warning"
              @click="exportUserList"
            >
              {{ t('manage.exports.actions.export') }}
            </ElButton>
          </template>
          <ZDataExport type="users" :model-value="true" />
        </ElPopover>
      </div>

      <ElTable
        ref="tableRef"
        v-loading="loading"
        :data="users"
        stripe
        row-key="_id"
        @selection-change="handleSelectionChange"
        :max-height="tableHeight"
      >
        <ElTableColumn v-if="selectable" type="selection" reserve-selection />
        <ElTableColumn
          prop="name"
          :label="t('manage.groupDetails.userList.columns.name')"
          :min-width="narrow ? 110 : 140"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="id"
          :label="t('manage.groupDetails.userList.columns.id')"
          :min-width="narrow ? 110 : 140"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="banned"
          :label="t('manage.groupDetails.userList.columns.status')"
          :min-width="narrow ? 80 : 100"
        >
          <template #default="{ row }">
            <span :class="row.banned ? 'color-red' : 'color-green'">
              {{ row.banned ? t('home.status.banned') : t('home.status.normal') }}
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="group"
          :label="t('manage.groupDetails.userList.columns.group')"
          :min-width="narrow ? 120 : 160"
        >
          <template #default="{ row }">
            <ZUserGroup v-for="group in row.group" :key="group" :group="group" class="px-1" />
          </template>
        </ElTableColumn>
        <ElTableColumn
          v-if="pwdm && !selectable"
          prop="password"
          :label="t('manage.groupDetails.userList.columns.pwdm')"
          min-width="120"
        >
          <template #default="{ row }">
            <span v-if="row.password" class="color-green">{{
              t('manage.groupDetails.userList.columns.pwdmStatus.true')
            }}</span>
            <span v-else class="color-red">{{
              t('manage.groupDetails.userList.columns.pwdmStatus.false')
            }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn fixed="right" :min-width="narrow ? 72 : 260">
          <template #header>
            <div v-if="!narrow">
              <ElInput
                v-if="searchOnly"
                v-model="search"
                size="small"
                :placeholder="t('manage.groupList.columns.search')"
                :prefix-icon="Search"
                @blur="handleSearch"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              />
              <ElRow class="w-full" v-else>
                <ElCol :span="16">
                  <ElInput
                    class="mx-1"
                    v-model="search"
                    size="small"
                    :placeholder="t('manage.groupList.columns.search')"
                    :prefix-icon="Search"
                    @blur="handleSearch"
                    @keyup.enter="handleSearch"
                    @clear="handleSearch"
                  />
                </ElCol>
                <ElCol :span="8">
                  <ElButton
                    v-if="id"
                    :icon="Box"
                    class="w-full mx-1"
                    @click="exportUserList"
                    text
                    bf
                    round
                    size="small"
                    type="warning"
                  >
                    {{ t('manage.exports.actions.export') }}
                  </ElButton>
                  <ElPopover v-else :width="320">
                    <template #reference>
                      <ElButton
                        :icon="Box"
                        class="w-full mx-1"
                        @click="exportUserList"
                        text
                        bf
                        round
                        size="small"
                        type="warning"
                      >
                        {{ t('manage.exports.actions.export') }}
                      </ElButton>
                    </template>
                    <ZDataExport type="users" :model-value="true" />
                  </ElPopover>
                </ElCol>
              </ElRow>
            </div>
          </template>
          <template #default="{ row }">
            <ElButton
              v-if="!selectable && !narrow"
              type="primary"
              text
              bg
              size="small"
              @click="router.push(`/user/${row._id}`)"
            >
              {{ t('manage.groupList.columns.details') }}
            </ElButton>
            <ElTooltip
              v-else-if="!selectable"
              :content="t('manage.groupList.columns.details')"
              placement="top"
            >
              <button
                type="button"
                class="yg-icon-btn yg-icon-btn--lg group-user-action"
                :aria-label="t('manage.groupList.columns.details')"
                @click="router.push(`/user/${row._id}`)"
              >
                <ElIcon :size="18"><View /></ElIcon>
              </button>
            </ElTooltip>
          </template>
        </ElTableColumn>
      </ElTable>
      <ZPagination
        class="py-1 pt-4"
        v-model:current-page="page"
        v-model:page-size="perpage"
        :total="total"
      />
    </ElCard>
  </div>
</template>

<style scoped>
/* 窄屏工具栏：搜索框与导出并排，间距走 4px 网格 */
.group-user-toolbar {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-3);
}

/* 行内图标按钮：主色，44px 触控目标 */
.group-user-action {
  color: var(--yg-color-primary);
}
</style>

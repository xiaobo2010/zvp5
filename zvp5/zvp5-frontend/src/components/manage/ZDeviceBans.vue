<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDebounceFn, useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElCard,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import type { FormInstance, FormItemRule, FormRules, TableInstance } from 'element-plus'
import {
  banDevice,
  getBannedDevices,
  resetDeviceGradient,
  unbanDevice,
  type BannedDevice
} from '@/api/ban'
import ZPagination from '@/components/utils/ZPagination.vue'
import { resolveErrorMessage } from '@/utils/errorMessage'
import { isReauthCancelled, withReauth } from '@/plugins/reauth'

const { t } = useI18n()

/**
 * 嵌入模式：组件被放进「封禁管理」页的页内分区里，
 * 不再自带页面级外壳（标题/最大宽度/额外上边距都由外层承担）。
 */
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })

const { width, height } = useWindowSize()

const narrow = computed(() => width.value <= 768)

const tableRef = ref<TableInstance>()

const BOTTOM_ROOM = 96

function computeTableHeight(table: TableInstance | undefined): number {
  const el = (table as { $el?: HTMLElement } | undefined)?.$el
  const top = el?.getBoundingClientRect?.().top ?? 0
  if (top > 0 && top < height.value * 0.8) {
    return Math.max(280, Math.round(height.value - top - BOTTOM_ROOM))
  }
  return Math.max(280, Math.round(height.value - 320))
}

const tableHeight = ref(280)

function measureTable() {
  tableHeight.value = computeTableHeight(tableRef.value)
}

watch([width, height], () => {
  measureTable()
})

const devices = ref<BannedDevice[]>([])
const loading = ref(false)
const failed = ref(false)
const page = ref(1)
const perpage = ref(20)
const total = ref(0)
const query = ref('')

const formRef = ref<FormInstance>()
const banning = ref(false)

const form = reactive({
  xuehai_id: '',
  reason: '',
  duration: 'forever'
})

const DEVICE_ID_PATTERN = /^\d{1,32}$/

const DURATION_DAYS: Record<string, number | null> = {
  '1': 1,
  '7': 7,
  '30': 30,
  forever: null
}

const durationOptions = computed(() => [
  { value: '1', label: t('manage.deviceBans.manual.durations.day1') },
  { value: '7', label: t('manage.deviceBans.manual.durations.day7') },
  { value: '30', label: t('manage.deviceBans.manual.durations.day30') },
  { value: 'forever', label: t('manage.deviceBans.manual.durations.forever') }
])

function validateXuehaiId(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!DEVICE_ID_PATTERN.test(text)) {
    callback(new Error(t('manage.deviceBans.manual.xuehaiIdInvalid')))
    return
  }
  callback()
}

function validateReason(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (text.length === 0) {
    callback(new Error(t('manage.deviceBans.manual.reasonRequired')))
    return
  }
  callback()
}

const formRules: FormRules = {
  xuehai_id: [{ validator: validateXuehaiId, trigger: 'blur' }] as FormItemRule[],
  reason: [{ validator: validateReason, trigger: 'blur' }] as FormItemRule[]
}

function untilOf(duration: string): string | null {
  const days = DURATION_DAYS[duration]
  if (days === undefined || days === null) {
    return null
  }
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString()
}

function periodLabel(duration: string): string {
  const option = durationOptions.value.find((item) => item.value === duration)
  return option?.label ?? ''
}

function formatTime(value?: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const fill = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${fill(date.getMonth() + 1)}-${fill(date.getDate())} ${fill(date.getHours())}:${fill(date.getMinutes())}`
}

function formatUntil(device: BannedDevice): string {
  if (!device.until) {
    return t('manage.deviceBans.table.permanent')
  }
  return formatTime(device.until)
}

function formatOwner(device: BannedDevice): string {
  return device.owner?.name || '—'
}

function formatReason(device: BannedDevice): string {
  return device.reason && device.reason.length > 0
    ? device.reason
    : t('manage.deviceBans.table.reasonUnknown')
}

async function loadDevices() {
  loading.value = true
  try {
    const result = await getBannedDevices(page.value, perpage.value, query.value.trim())
    devices.value = result.devices
    total.value = result.total
    failed.value = false
  } catch {
    devices.value = []
    total.value = 0
    failed.value = true
  } finally {
    loading.value = false
  }
}

function searchDevices() {
  if (page.value === 1) {
    void loadDevices()
  } else {
    page.value = 1
  }
}

const debouncedSearch = useDebounceFn(searchDevices, 300)

watch(query, () => {
  debouncedSearch()
})

watch(page, () => {
  void loadDevices()
})

watch(perpage, () => {
  if (page.value === 1) {
    void loadDevices()
  } else {
    page.value = 1
  }
})

onMounted(() => {
  void loadDevices()
  void nextTick(() => {
    measureTable()
  })
})

async function submitBan() {
  if (!formRef.value) {
    return
  }
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }
  const xuehaiId = form.xuehai_id.trim()
  const period = periodLabel(form.duration)
  try {
    await ElMessageBox.confirm(
      t('manage.deviceBans.manual.confirm', { xuehaiId, period }),
      t('manage.deviceBans.manual.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.deviceBans.manual.confirmButton'),
        cancelButtonText: t('manage.deviceBans.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  banning.value = true
  try {
    await withReauth(t('manage.deviceBans.reauthReason'), (token) =>
      banDevice(
        {
          xuehai_id: xuehaiId,
          reason: form.reason.trim(),
          until: untilOf(form.duration)
        },
        token
      )
    )
    ElMessage.success(t('manage.deviceBans.manual.bannedNotice', { xuehaiId }))
    form.xuehai_id = ''
    form.reason = ''
    form.duration = 'forever'
    void nextTick(() => formRef.value?.clearValidate())
    searchDevices()
  } catch (err) {
    if (isReauthCancelled(err)) {
      ElMessage.info(t('manage.reauth.cancelled'))
      return
    }
    ElMessage.error(resolveErrorMessage(err, 'manage.deviceBans.manual.banFailed'))
  } finally {
    banning.value = false
  }
}

async function confirmUnban(device: BannedDevice) {
  const xuehaiId = device.xuehai_id
  try {
    await ElMessageBox.confirm(
      t('manage.deviceBans.table.unbanConfirm', { xuehaiId }),
      t('manage.deviceBans.table.unbanConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.deviceBans.table.unbanConfirmButton'),
        cancelButtonText: t('manage.deviceBans.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await withReauth(t('manage.deviceBans.reauthReason'), (token) =>
      unbanDevice(xuehaiId, token)
    )
    ElMessage.success(t('manage.deviceBans.table.unbannedNotice', { xuehaiId }))
    void loadDevices()
  } catch (err) {
    if (isReauthCancelled(err)) {
      ElMessage.info(t('manage.reauth.cancelled'))
      return
    }
    ElMessage.error(resolveErrorMessage(err, 'manage.deviceBans.table.unbanFailed'))
  }
}

async function confirmReset(device: BannedDevice) {
  const xuehaiId = device.xuehai_id
  try {
    await ElMessageBox.confirm(
      t('manage.deviceBans.table.resetConfirm', { xuehaiId }),
      t('manage.deviceBans.table.resetConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.deviceBans.table.resetConfirmButton'),
        cancelButtonText: t('manage.deviceBans.cancel'),
        confirmButtonClass: 'el-button--warning',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await withReauth(t('manage.deviceBans.reauthReason'), (token) =>
      resetDeviceGradient(xuehaiId, token)
    )
    ElMessage.success(t('manage.deviceBans.table.resetNotice', { xuehaiId }))
    void loadDevices()
  } catch (err) {
    if (isReauthCancelled(err)) {
      ElMessage.info(t('manage.reauth.cancelled'))
      return
    }
    ElMessage.error(resolveErrorMessage(err, 'manage.deviceBans.table.resetFailed'))
  }
}
</script>

<template>
  <div
    class="device-bans flex flex-col gap-4"
    :class="{ 'device-bans--embedded': props.embedded }"
  >
    <p class="device-bans__hint">{{ t('manage.deviceBans.hint') }}</p>

    <ElCard shadow="never">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <span>{{ t('manage.deviceBans.manual.title') }}</span>
        </div>
      </template>
      <ElForm
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        class="device-bans__form"
      >
        <ElFormItem
          :label="t('manage.deviceBans.manual.xuehaiIdLabel')"
          prop="xuehai_id"
          class="device-bans__field device-bans__field--id"
        >
          <ElInput
            v-model="form.xuehai_id"
            :placeholder="t('manage.deviceBans.manual.xuehaiIdPlaceholder')"
            maxlength="32"
            inputmode="numeric"
            clearable
          />
        </ElFormItem>
        <ElFormItem
          :label="t('manage.deviceBans.manual.reasonLabel')"
          prop="reason"
          class="device-bans__field device-bans__field--reason"
        >
          <ElInput
            v-model="form.reason"
            :placeholder="t('manage.deviceBans.manual.reasonPlaceholder')"
            maxlength="128"
            clearable
          />
        </ElFormItem>
        <ElFormItem
          :label="t('manage.deviceBans.manual.durationLabel')"
          prop="duration"
          class="device-bans__field device-bans__field--duration"
        >
          <ElSelect v-model="form.duration">
            <ElOption
              v-for="option in durationOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem class="device-bans__field device-bans__field--submit">
          <ElButton type="danger" :loading="banning" @click="submitBan">
            {{ t('manage.deviceBans.manual.submit') }}
          </ElButton>
        </ElFormItem>
      </ElForm>
    </ElCard>

    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span>{{ t('manage.deviceBans.table.title') }}</span>
          <div class="flex flex-wrap items-center gap-2">
            <ElInput
              v-model="query"
              :placeholder="t('manage.deviceBans.table.searchPlaceholder')"
              clearable
              :class="narrow ? 'w-full' : 'w-56'"
              @keyup.enter="searchDevices"
              @clear="searchDevices"
            />
            <ElButton size="small" @click="searchDevices">
              {{ t('manage.deviceBans.table.search') }}
            </ElButton>
            <ElButton text size="small" @click="loadDevices">
              {{ t('manage.deviceBans.refresh') }}
            </ElButton>
          </div>
        </div>
      </template>
      <ElTable
        ref="tableRef"
        v-loading="loading"
        :data="devices"
        row-key="xuehai_id"
        stripe
        :max-height="tableHeight"
      >
        <ElTableColumn
          prop="xuehai_id"
          :label="t('manage.deviceBans.table.columns.xuehaiId')"
          :min-width="narrow ? 110 : 130"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ row.xuehai_id }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="reason"
          :label="t('manage.deviceBans.table.columns.reason')"
          :min-width="narrow ? 150 : 200"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span :class="{ 'device-bans__muted': !row.reason }">{{ formatReason(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="source"
          :label="t('manage.deviceBans.table.columns.source')"
          :min-width="narrow ? 90 : 100"
        >
          <template #default="{ row }">
            <ElTag size="small" :type="row.source === 'admin' ? 'danger' : 'warning'">
              {{
                row.source === 'admin'
                  ? t('manage.deviceBans.table.sourceAdmin')
                  : t('manage.deviceBans.table.sourceRule')
              }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="rule_code"
          :label="t('manage.deviceBans.table.columns.ruleCode')"
          :min-width="narrow ? 110 : 140"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span>{{ row.rule_code || '—' }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="until"
          :label="t('manage.deviceBans.table.columns.until')"
          :min-width="narrow ? 140 : 150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatUntil(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="ban_count"
          :label="t('manage.deviceBans.table.columns.banCount')"
          :min-width="narrow ? 90 : 100"
          align="center"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ row.ban_count }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="owner"
          :label="t('manage.deviceBans.table.columns.owner')"
          :min-width="narrow ? 100 : 120"
        >
          <template #default="{ row }">
            <span :class="{ 'device-bans__muted': !row.owner }">{{ formatOwner(row) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="created_at"
          :label="t('manage.deviceBans.table.columns.createdAt')"
          :min-width="narrow ? 140 : 150"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatTime(row.created_at) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn fixed="right" :width="narrow ? 150 : 170" align="right">
          <template #default="{ row }">
            <div class="flex items-center justify-end gap-1">
              <ElButton text type="warning" size="small" @click="confirmReset(row)">
                {{ t('manage.deviceBans.table.reset') }}
              </ElButton>
              <ElButton text type="danger" size="small" @click="confirmUnban(row)">
                {{ t('manage.deviceBans.table.unban') }}
              </ElButton>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="
              failed ? t('manage.deviceBans.loadFailed') : t('manage.deviceBans.table.empty')
            "
            :image-size="100"
          />
        </template>
      </ElTable>
      <ZPagination
        v-model:current-page="page"
        v-model:page-size="perpage"
        :total="total"
      />
    </ElCard>
  </div>
</template>

<style scoped>
/* 组件根：撑满外层容器宽度，不自带页面级最大宽度 */
.device-bans {
  width: 100%;
}

/* 嵌入外层分区时不再叠加上边距，间距由外层的 flex gap 统一控制 */
.device-bans--embedded {
  margin-top: 0;
}

.device-bans__hint {
  margin: 0;
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.device-bans__form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--yg-space-3);
}

.device-bans__field {
  margin-bottom: 0;
}

.device-bans__field--id {
  flex: 0 0 12rem;
  max-width: 100%;
}

.device-bans__field--reason {
  flex: 1 1 16rem;
  min-width: 0;
}

.device-bans__field--duration {
  flex: 0 0 9rem;
}

.device-bans__field--submit {
  flex: 0 0 auto;
}

.device-bans__muted {
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
}

@media (max-width: 768px) {
  .device-bans__field--id,
  .device-bans__field--duration {
    flex: 1 1 8rem;
  }

  .device-bans__field--reason {
    flex: 1 1 100%;
  }
}
</style>

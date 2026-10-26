<script setup lang="ts">
/**
 * 用户配额对话框（用户管理页的行内入口）。
 *
 * 两块内容各自独立保存：
 * a. AI 额度：OCR / chat 两行，改 limit_count 走 PUT /ai/quota/{oid}，重置用量走
 *    POST /ai/quota/{oid}/reset（破坏性，二次确认）。额度按自然月计算，设置只影响
 *    当月，换月按角色默认重建 —— 这句必须写在界面上。
 * b. 附件每日上传：daily_max_files / daily_max_bytes，空值表示继承全局；字节与 MB
 *    的换算在 userQuotaUnits.ts 里，组件只负责取值。
 *
 * 两条接口分属不同车道：附件配额接口缺失时只降级这一块，AI 额度照常可改。
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElDialog,
  ElIcon,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElTag
} from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  aiErrorCodeOf,
  aiErrorCopy,
  aiQuotaSourceLabelKey,
  resetUserQuota,
  setUserLimit,
  userQuota,
  type AiQuotaEntryView,
  type AiQuotaSnapshotView
} from '@/api/ai'
import {
  getUserQuota,
  setUserQuota,
  type UserQuotaView
} from '@/api/system/userQuotas'
import { bytesToMb, formatMb, mbToBytes } from './userQuotaUnits'
import type { AiQuotaKind } from '@/../types/ai'

const props = defineProps<{
  visible: boolean
  /** 目标用户：oid 用于接口，name 只用于标题区展示。 */
  user: { oid: string; name: string } | null
}>()

const emit = defineEmits<{
  (event: 'update:visible', value: boolean): void
}>()

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
/** 附件配额单独降级：该接口由另一条车道提供，未部署时不影响 AI 额度。 */
const uploadFailed = ref(false)

const aiQuota = ref<AiQuotaSnapshotView | null>(null)
const uploadQuota = ref<UserQuotaView | null>(null)

/** 上限草稿；null = 输入框被清空（提交前会被拦下）。 */
const limitDraft = ref<Record<AiQuotaKind, number | null>>({ ocr: 0, chat: 0 })
const savingKind = ref<AiQuotaKind | ''>('')

/** 覆盖值草稿：null 表示留空 → 提交 null → 继承全局。 */
const filesDraft = ref<number | null>(null)
const bytesMbDraft = ref<number | null>(null)
const savingUpload = ref(false)

const aiRows = computed(() => [
  { kind: 'ocr' as AiQuotaKind, title: t('ai.quotaAdmin.ocrTitle'), entry: aiQuota.value?.ocr },
  { kind: 'chat' as AiQuotaKind, title: t('ai.quotaAdmin.chatTitle'), entry: aiQuota.value?.chat }
])

const effectiveText = computed(() => {
  const effective = uploadQuota.value?.effective
  if (!effective) {
    return ''
  }
  return t('ai.userQuota.effective', {
    files: effective.daily_max_files,
    bytes: formatMb(effective.daily_max_bytes)
  })
})

function sourceLabel(entry: AiQuotaEntryView | null | undefined): string | null {
  const key = aiQuotaSourceLabelKey(entry?.source)
  return key ? t(key) : null
}

function reportError(error: unknown) {
  const copy = aiErrorCopy(aiErrorCodeOf(error), t)
  ElMessage.error(`${copy.title}：${copy.hint}`)
}

async function load() {
  const oid = props.user?.oid
  if (!oid) {
    return
  }
  loading.value = true
  failed.value = false
  uploadFailed.value = false
  try {
    const ai = await userQuota(oid)
    aiQuota.value = ai
    limitDraft.value = { ocr: ai.ocr.limit, chat: ai.chat.limit }
  } catch (error) {
    aiQuota.value = null
    failed.value = true
    reportError(error)
  }

  // 附件配额单独 try：另一条车道的接口不存在时，AI 额度仍然可用。
  try {
    const upload = await getUserQuota(oid)
    uploadQuota.value = upload
    filesDraft.value = upload.daily_max_files
    bytesMbDraft.value = bytesToMb(upload.daily_max_bytes)
  } catch {
    uploadQuota.value = null
    filesDraft.value = null
    bytesMbDraft.value = null
    uploadFailed.value = true
  } finally {
    loading.value = false
  }
}

async function saveLimit(kind: AiQuotaKind) {
  const oid = props.user?.oid
  const value = limitDraft.value[kind]
  if (!oid || savingKind.value) {
    return
  }
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) {
    ElMessage.error(
      `${t('ai.errors.AI_QUOTA_BAD_LIMIT.title')}：${t('ai.errors.AI_QUOTA_BAD_LIMIT.hint')}`
    )
    return
  }
  savingKind.value = kind
  try {
    await setUserLimit(oid, kind, Math.trunc(value))
    ElMessage.success(t('ai.userQuota.savedAi'))
    await load()
  } catch (error) {
    reportError(error)
  } finally {
    savingKind.value = ''
  }
}

async function resetUsage(kind: AiQuotaKind) {
  const oid = props.user?.oid
  if (!oid) {
    return
  }
  try {
    await ElMessageBox.confirm(
      t('ai.quotaAdmin.resetConfirm'),
      t('ai.quotaAdmin.resetConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('ai.common.confirm'),
        cancelButtonText: t('ai.common.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }
  try {
    await resetUserQuota(oid, kind)
    ElMessage.success(t('ai.quotaAdmin.resetNotice'))
    await load()
  } catch (error) {
    reportError(error)
  }
}

/** 空输入 → null（继承全局）；否则取整并保证非负。 */
function normalizeCount(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null
  }
  return Math.max(0, Math.trunc(value))
}

async function saveUpload() {
  const oid = props.user?.oid
  if (!oid || savingUpload.value) {
    return
  }
  savingUpload.value = true
  try {
    const saved = await setUserQuota(oid, {
      daily_max_files: normalizeCount(filesDraft.value),
      // MB → 字节：后端字段单位是字节。
      daily_max_bytes: mbToBytes(bytesMbDraft.value)
    })
    uploadQuota.value = saved
    filesDraft.value = saved.daily_max_files
    bytesMbDraft.value = bytesToMb(saved.daily_max_bytes)
    uploadFailed.value = false
    ElMessage.success(t('ai.userQuota.savedUpload'))
  } catch (error) {
    reportError(error)
  } finally {
    savingUpload.value = false
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      aiQuota.value = null
      uploadQuota.value = null
      void load()
    }
  },
  { immediate: true }
)
</script>

<template>
  <ElDialog
    :model-value="visible"
    :title="t('ai.userQuota.title')"
    width="min(560px, 92vw)"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" class="user-quota">
      <header class="user-quota__head">
        <div class="flex flex-col min-w-0">
          <span class="yg-text-md">{{ user?.name || '—' }}</span>
          <span class="yg-text-xs yg-text-tertiary">{{ user?.oid || '—' }}</span>
        </div>
        <ElButton text size="small" :disabled="loading" @click="load">
          <ElIcon class="mr-1"><Refresh /></ElIcon>
          {{ t('ai.common.refresh') }}
        </ElButton>
      </header>

      <p v-if="failed" class="yg-text-sm yg-text-danger">{{ t('ai.userQuota.loadFailed') }}</p>

      <template v-else>
        <section class="user-quota__section">
          <h3 class="user-quota__heading">{{ t('ai.userQuota.aiSection') }}</h3>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.userQuota.monthlyNotice') }}</p>

          <div v-for="item in aiRows" :key="item.kind" class="user-quota__row">
            <div class="user-quota__row-head">
              <span class="yg-text-sm">{{ item.title }}</span>
              <ElTag v-if="sourceLabel(item.entry)" type="info" size="small" effect="plain">
                {{ sourceLabel(item.entry) }}
              </ElTag>
              <span class="user-quota__usage tabular-nums">
                {{ t('ai.quota.used') }} {{ item.entry?.used ?? 0 }} / {{ item.entry?.limit ?? 0 }}
              </span>
            </div>
            <div class="user-quota__row-body">
              <ElInputNumber
                v-model="limitDraft[item.kind]"
                :min="0"
                :step="10"
                :precision="0"
                class="w-full"
              />
              <div class="flex items-center justify-end gap-2">
                <!-- 清零属于破坏性操作：红字 + 二次确认 -->
                <ElButton size="small" text type="danger" @click="resetUsage(item.kind)">
                  {{ t('ai.quotaAdmin.reset') }}
                </ElButton>
                <ElButton
                  size="small"
                  type="primary"
                  plain
                  :loading="savingKind === item.kind"
                  @click="saveLimit(item.kind)"
                >
                  {{ t('ai.common.save') }}
                </ElButton>
              </div>
            </div>
          </div>
        </section>

        <section class="user-quota__section">
          <h3 class="user-quota__heading">{{ t('ai.userQuota.uploadSection') }}</h3>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.userQuota.inheritHint') }}</p>
          <p v-if="uploadFailed" class="yg-text-xs yg-text-tertiary">
            {{ t('ai.userQuota.loadFailedHint') }}
          </p>

          <!-- 窄屏单列堆叠；≥640px 才并排，避免两个数字输入框挤成一行 -->
          <div class="user-quota__pair">
            <label class="user-quota__field">
              <span class="yg-text-xs yg-text-tertiary">
                {{ t('ai.userQuota.dailyMaxFiles') }}
              </span>
              <ElInputNumber
                v-model="filesDraft"
                :min="0"
                :step="1"
                :precision="0"
                :placeholder="t('ai.userQuota.filesPlaceholder')"
                class="w-full"
              />
            </label>
            <label class="user-quota__field">
              <span class="yg-text-xs yg-text-tertiary">
                {{ t('ai.userQuota.dailyMaxBytes') }}（{{ t('ai.userQuota.dailyMaxBytesUnit') }}）
              </span>
              <ElInputNumber
                v-model="bytesMbDraft"
                :min="0"
                :step="10"
                :precision="1"
                :placeholder="t('ai.userQuota.bytesPlaceholder')"
                class="w-full"
              />
            </label>
          </div>

          <p v-if="effectiveText" class="yg-text-xs yg-text-tertiary">
            {{ effectiveText }}
          </p>
          <div class="flex justify-end">
            <ElButton type="primary" :loading="savingUpload" @click="saveUpload">
              {{ t('ai.common.save') }}
            </ElButton>
          </div>
        </section>
      </template>
    </div>

    <template #footer>
      <ElButton @click="emit('update:visible', false)">
        {{ t('ai.common.close') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.user-quota {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

.user-quota__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg-space-3);
}

.user-quota__section {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
}

.user-quota__heading {
  margin: 0;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-semibold);
  color: var(--yg-text-primary);
}

.user-quota__row {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding-top: var(--yg-space-2);
  border-top: 1px solid var(--yg-border-subtle, var(--yg-border-color));
}

.user-quota__row-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

/* 用量靠右对齐，标签总数不多，不抢戏 */
.user-quota__usage {
  margin-left: auto;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
  white-space: nowrap;
}

.user-quota__row-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
}

/* 默认单列；宽屏才并排 */
.user-quota__pair {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--yg-space-2);
}

.user-quota__field {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

@media (min-width: 640px) {
  .user-quota__pair {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .user-quota__row-body {
    flex-direction: row;
    align-items: center;
  }

  .user-quota__row-body > .el-input-number {
    flex: 1 1 auto;
    min-width: 0;
  }
}
</style>

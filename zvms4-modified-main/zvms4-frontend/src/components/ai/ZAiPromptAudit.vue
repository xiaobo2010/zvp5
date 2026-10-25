<script setup lang="ts">
/**
 * 行为监控（admin）：受控留存的 prompt 审阅。
 *
 * 列表只拿元数据——后端在这一层刻意不 SELECT 正文；点开某一行才读正文，而详情
 * 端点会在同一次请求里写一条访问审计。所以抽屉顶部必须显式说明「这次查看会被
 * 记录」：不是吓人的警告，而是让管理员知道自己的操作留痕。
 *
 * 视觉与交互沿用 ZAiLogsAdmin：同款卡片/筛选栅格/ElTable + ZPagination，不另起
 * 一套页面语言。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn
} from 'element-plus'
import { CopyDocument, InfoFilled, Refresh, Search } from '@element-plus/icons-vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import {
  AI_PROMPT_DAYS_DEFAULT,
  AI_PROMPT_DAYS_OPTIONS,
  aiErrorCodeOf,
  aiErrorCopy,
  getAiPrompt,
  listAiPrompts
} from '@/api/ai'
import { formatDateTime } from '@/utils/dateTime'
import type { AiPromptAuditDetail, AiPromptAuditRecord } from '@/../types/ai'

const { t } = useI18n()

const rows = ref<AiPromptAuditRecord[]>([])
const total = ref(0)
const page = ref(1)
const perpage = ref(20)
const loading = ref(false)
const loadFailed = ref(false)

/**
 * 天数改动立刻生效（与统计概览的天数控件一致）；用户 oid 需要点「查询」才生效，
 * 否则每敲一个字符就打一次接口，且半截 oid 只会返回 400。
 */
const filters = reactive({
  userOid: '',
  days: AI_PROMPT_DAYS_DEFAULT
})

/** 已经生效的 oid 过滤（查询时从 filters 抄一份）。 */
const appliedUserOid = ref('')

/** 依次递增的请求序号：慢响应回来时若已有更新的请求，直接丢弃。 */
let listSeq = 0
let detailSeq = 0

async function load() {
  const seq = ++listSeq
  loading.value = true
  try {
    const result = await listAiPrompts({
      page: page.value,
      perpage: perpage.value,
      userOid: appliedUserOid.value || undefined,
      days: filters.days
    })
    if (seq !== listSeq) {
      return
    }
    rows.value = result.rows
    total.value = result.total
    loadFailed.value = false
  } catch (error) {
    if (seq !== listSeq) {
      return
    }
    rows.value = []
    total.value = 0
    loadFailed.value = true
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  } finally {
    if (seq === listSeq) {
      loading.value = false
    }
  }
}

function query() {
  appliedUserOid.value = filters.userOid.trim()
  page.value = 1
  void load()
}

function onPageChange(nextPage: number, nextSize: number) {
  page.value = nextPage
  perpage.value = nextSize
  void load()
}

watch(
  () => filters.days,
  () => {
    page.value = 1
    void load()
  }
)

const emptyDescription = computed(() =>
  loadFailed.value ? t('ai.prompts.loadFailed') : t('ai.prompts.empty')
)

// ---------------------------------------------------------------------------
// 详情抽屉：一次查看 = 一条访问审计
// ---------------------------------------------------------------------------

const drawerOpen = ref(false)
const detail = ref<AiPromptAuditDetail | null>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)
const activeOid = ref('')

/** 抽屉宽度：窄屏占满，桌面放宽到近半屏，正文才能一行读得下去。 */
const { width } = useWindowSize()
const drawerSize = computed(() => (width.value < 768 ? '100%' : '48%'))

const detailText = computed(() => detail.value?.prompt_text ?? '')

async function loadDetail(oid: string) {
  const seq = ++detailSeq
  detailLoading.value = true
  detailFailed.value = false
  try {
    const result = await getAiPrompt(oid)
    if (seq !== detailSeq) {
      return
    }
    detail.value = result
  } catch (error) {
    if (seq !== detailSeq) {
      return
    }
    detail.value = null
    detailFailed.value = true
    const copy = aiErrorCopy(aiErrorCodeOf(error), t)
    ElMessage.error(`${copy.title}：${copy.hint}`)
  } finally {
    if (seq === detailSeq) {
      detailLoading.value = false
    }
  }
}

function openDetail(row: AiPromptAuditRecord) {
  activeOid.value = row.oid
  detail.value = null
  drawerOpen.value = true
  void loadDetail(row.oid)
}

function retryDetail() {
  if (activeOid.value) {
    void loadDetail(activeOid.value)
  }
}

async function copyBody() {
  const text = detailText.value
  if (!text) {
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t('ai.common.copied'))
  } catch {
    // 失败时不把正文塞进 toast：那是一段学生对话，不该在通知条里再露一次。
    ElMessage.warning(t('ai.prompts.copyBlocked'))
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="yg-card">
      <header class="yg-card__header">
        <div class="flex flex-col gap-1 min-w-0">
          <h2 class="yg-card__title">{{ t('ai.prompts.title') }}</h2>
          <p class="yg-text-xs yg-text-tertiary">{{ t('ai.prompts.subtitle') }}</p>
        </div>
        <div class="yg-card__extra flex flex-wrap items-center gap-2">
          <ElButton :loading="loading" @click="load">
            <ElIcon class="mr-1"><Refresh /></ElIcon>
            {{ t('ai.common.refresh') }}
          </ElButton>
        </div>
      </header>

      <div class="yg-card__body">
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.prompts.userOid') }}</span>
            <ElInput
              v-model="filters.userOid"
              clearable
              :placeholder="t('ai.prompts.userOidPlaceholder')"
              @keydown.enter="query"
            />
          </div>
          <div class="flex flex-col gap-1">
            <span class="yg-text-xs yg-text-tertiary">{{ t('ai.prompts.days') }}</span>
            <ElSelect v-model="filters.days" class="ai-prompt-audit__days">
              <ElOption
                v-for="item in AI_PROMPT_DAYS_OPTIONS"
                :key="item"
                :label="`${item} ${t('ai.prompts.daysUnit')}`"
                :value="item"
              />
            </ElSelect>
          </div>
          <div class="flex items-end">
            <ElButton type="primary" :loading="loading" :icon="Search" @click="query">
              {{ t('ai.prompts.query') }}
            </ElButton>
          </div>
        </div>
        <p class="yg-text-xs yg-text-tertiary mt-3">{{ t('ai.prompts.daysHint') }}</p>
      </div>
    </section>

    <section class="yg-card">
      <div class="yg-card__body">
        <ElTable v-loading="loading" :data="rows" row-key="oid" stripe @row-click="openDetail">
          <ElTableColumn :label="t('ai.prompts.columns.createdAt')" min-width="150">
            <template #default="{ row }">
              <span class="tabular-nums yg-text-xs">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.prompts.columns.user')" min-width="170">
            <template #default="{ row }">
              <div class="flex flex-col">
                <span class="yg-text-sm">{{ row.user_name || '—' }}</span>
                <span class="yg-text-xs yg-text-tertiary">{{ row.user_oid || '—' }}</span>
              </div>
            </template>
          </ElTableColumn>
          <ElTableColumn
            :label="t('ai.prompts.columns.roles')"
            min-width="150"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span v-if="row.roles_snapshot" class="yg-text-xs">{{ row.roles_snapshot }}</span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn :label="t('ai.prompts.columns.purpose')" min-width="140">
            <template #default="{ row }">
              <span v-if="row.purpose" class="yg-text-xs">{{ row.purpose }}</span>
              <span v-else class="yg-text-tertiary">—</span>
            </template>
          </ElTableColumn>
          <ElTableColumn
            prop="prompt_chars"
            :label="t('ai.prompts.columns.chars')"
            width="110"
            align="right"
          >
            <template #default="{ row }">
              <span class="tabular-nums">{{ row.prompt_chars }}</span>
            </template>
          </ElTableColumn>
          <!-- 行本身可点；补一个显式按钮，键盘用户与触屏都不必猜「这一行能点」 -->
          <ElTableColumn :label="t('ai.common.actions')" width="88" align="center">
            <template #default="{ row }">
              <ElButton text size="small" @click.stop="openDetail(row)">
                {{ t('ai.prompts.view') }}
              </ElButton>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :description="emptyDescription" :image-size="90">
              <ElButton v-if="loadFailed" @click="load">{{ t('ai.prompts.retry') }}</ElButton>
            </ElEmpty>
          </template>
        </ElTable>

        <ZPagination
          v-model:current-page="page"
          v-model:page-size="perpage"
          :total="total"
          @change="onPageChange"
        />
      </div>
    </section>

    <ElDrawer
      v-model="drawerOpen"
      direction="rtl"
      :size="drawerSize"
      :title="t('ai.prompts.detailTitle')"
    >
      <div class="flex flex-col gap-4">
        <!-- 访问审计提示：查看正文会被记录，先讲清楚再展示内容 -->
        <div class="yg-alert yg-alert--info">
          <ElIcon class="yg-alert__icon"><InfoFilled /></ElIcon>
          <span class="yg-alert__title">{{ t('ai.prompts.accessNotice') }}</span>
          <span class="yg-alert__desc">{{ t('ai.prompts.accessNoticeHint') }}</span>
        </div>

        <div class="ai-prompt-audit__meta">
          <span class="ai-prompt-audit__meta-label">{{ t('ai.prompts.detail.user') }}</span>
          <span class="ai-prompt-audit__meta-value">
            {{ detail?.user_name || '—' }}
            <span class="yg-text-xs yg-text-tertiary">{{ detail?.user_oid || '—' }}</span>
          </span>
          <span class="ai-prompt-audit__meta-label">{{ t('ai.prompts.detail.purpose') }}</span>
          <span class="ai-prompt-audit__meta-value">{{ detail?.purpose || '—' }}</span>
          <span class="ai-prompt-audit__meta-label">{{ t('ai.prompts.detail.roles') }}</span>
          <span class="ai-prompt-audit__meta-value">{{ detail?.roles_snapshot || '—' }}</span>
          <span class="ai-prompt-audit__meta-label">{{ t('ai.prompts.detail.createdAt') }}</span>
          <span class="ai-prompt-audit__meta-value tabular-nums">
            {{ formatDateTime(detail?.created_at) }}
          </span>
          <span class="ai-prompt-audit__meta-label">{{ t('ai.prompts.detail.requestId') }}</span>
          <span class="ai-prompt-audit__meta-value ai-prompt-audit__mono">
            {{ detail?.request_id || '—' }}
          </span>
        </div>

        <div class="flex items-center justify-between gap-2">
          <h3 class="yg-text-sm">{{ t('ai.prompts.body') }}</h3>
          <ElButton text size="small" :disabled="!detailText" @click="copyBody">
            <ElIcon class="mr-1"><CopyDocument /></ElIcon>
            {{ t('ai.prompts.copyBody') }}
          </ElButton>
        </div>

        <div v-loading="detailLoading" class="ai-prompt-audit__body">
          <template v-if="detailFailed">
            <p class="yg-text-sm yg-text-tertiary">{{ t('ai.prompts.detailFailed') }}</p>
            <ElButton size="small" @click="retryDetail">{{ t('ai.prompts.retry') }}</ElButton>
          </template>
          <p v-else-if="!detailLoading && !detailText" class="yg-text-sm yg-text-tertiary">
            {{ t('ai.prompts.noBody') }}
          </p>
          <pre v-else class="ai-prompt-audit__text">{{ detailText }}</pre>
        </div>
      </div>
    </ElDrawer>
  </div>
</template>

<style scoped>
.ai-prompt-audit__days {
  width: 110px;
}

/* 元数据：左列标签、右列取值；长 oid 与角色快照允许换行，不撑破抽屉。 */
.ai-prompt-audit__meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--yg-space-2) var(--yg-space-4);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
}

.ai-prompt-audit__meta-label {
  color: var(--yg-text-tertiary);
}

.ai-prompt-audit__meta-value {
  min-width: 0;
  color: var(--yg-text-primary);
  overflow-wrap: anywhere;
}

.ai-prompt-audit__mono {
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
}

/* 正文区：加载遮罩需要定位上下文 */
.ai-prompt-audit__body {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  min-height: var(--yg-space-10);
}

/* 等宽 + pre-wrap：保留模型输入里的换行与缩进，同时按容器宽度折行 */
.ai-prompt-audit__text {
  margin: 0;
  padding: var(--yg-space-3);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-relaxed);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>

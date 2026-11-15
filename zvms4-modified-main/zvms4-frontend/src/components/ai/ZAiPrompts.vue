<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  AI_PROMPT_DAYS_DEFAULT,
  AI_PROMPT_DAYS_OPTIONS,
  getAiPrompt,
  listAiPrompts
} from '@/api/next/ai/prompts'
import type { AiPromptAuditDetail, AiPromptAuditRecord } from '@/api/next/ai-types'
import { errorMessage } from '@/utils/errorMessage'

const rows = ref<AiPromptAuditRecord[]>([])
const total = ref(0)
const page = ref(1)
const perpage = 20
const days = ref(AI_PROMPT_DAYS_DEFAULT)
const purpose = ref('')
const keyword = ref('')
const loading = ref(false)
const error = ref('')

const detail = ref<AiPromptAuditDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listAiPrompts({
      page: page.value,
      perpage,
      days: days.value,
      purpose: purpose.value.trim() || undefined,
      keyword: keyword.value.trim() || undefined
    })
    rows.value = data.rows
    total.value = data.total
  } catch (e) {
    error.value = errorMessage(e, '提示词记录加载失败')
    rows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function openDetail(row: AiPromptAuditRecord) {
  detailLoading.value = true
  detailError.value = ''
  detail.value = { ...row, request_id: null, prompt_text: null, answer_text: null, tool_calls: null, refused: 0, warned: 0 }
  try {
    detail.value = await getAiPrompt(row.oid)
  } catch (e) {
    detailError.value = errorMessage(e, '正文读取失败')
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
}

function search() {
  page.value = 1
  void load()
}

function turn(delta: number) {
  const next = page.value + delta
  if (next < 1 || next > Math.max(1, Math.ceil(total.value / perpage))) return
  page.value = next
  void load()
}

onMounted(load)
</script>

<template>
  <section class="yg2-panel pr">
    <header class="pr__head">
      <div>
        <h2 class="yg2-card__title">提示词审阅</h2>
        <p class="pr__hint">
          列表只带元数据；点开一条才会读取正文，而这次读取本身会写入访问审计。
        </p>
      </div>
      <div class="yg2-toolbar pr__filters">
        <select v-model.number="days" class="yg2-input pr__days" @change="search">
          <option v-for="d in AI_PROMPT_DAYS_OPTIONS" :key="d" :value="d">近 {{ d }} 天</option>
        </select>
        <input v-model="purpose" class="yg2-input" placeholder="场景，如 ai.chat" @keyup.enter="search" />
        <input v-model="keyword" class="yg2-input" placeholder="正文包含…" @keyup.enter="search" />
        <button class="yg2-btn yg2-btn--secondary" type="button" @click="search">搜索</button>
      </div>
    </header>

    <p v-if="error" class="pr__error">{{ error }}</p>

    <div class="pr__table-wrap">
      <table class="pr__table">
        <thead>
          <tr>
            <th>时间</th>
            <th>用户</th>
            <th>场景</th>
            <th class="pr__num">正文长度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="4" class="pr__empty">加载中…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="4" class="pr__empty">这段时间没有记录</td>
          </tr>
          <tr
            v-for="row in rows"
            v-else
            :key="row.oid"
            tabindex="0"
            @click="openDetail(row)"
            @keydown.enter="openDetail(row)"
          >
            <td class="pr__num">{{ row.created_at?.slice(0, 16).replace('T', ' ') }}</td>
            <td>{{ row.user_name || row.user_oid || '未知用户' }}</td>
            <td>{{ row.purpose || '—' }}</td>
            <td class="pr__num">{{ row.prompt_chars }} 字</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="pr__foot">
      <span>共 {{ total }} 条</span>
      <div class="pr__pager">
        <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" :disabled="page <= 1" @click="turn(-1)">
          上一页
        </button>
        <span>第 {{ page }} / {{ Math.max(1, Math.ceil(total / perpage)) }} 页</span>
        <button
          class="yg2-btn yg2-btn--quiet yg2-btn--sm"
          type="button"
          :disabled="page >= Math.max(1, Math.ceil(total / perpage))"
          @click="turn(1)"
        >
          下一页
        </button>
      </div>
    </footer>

    <Teleport to="body">
      <div v-if="detail" class="pr-modal" role="dialog" aria-modal="true" @click.self="closeDetail">
        <div class="pr-modal__card">
          <div class="pr-modal__head">
            <h3 class="pr-modal__title">{{ detail.user_name || '提示词详情' }}</h3>
            <button class="yg2-icon-btn" type="button" aria-label="关闭" @click="closeDetail">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="m6 6 12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
          <p class="pr-modal__meta">
            {{ detail.created_at?.slice(0, 16).replace('T', ' ') }} · {{ detail.purpose || '未知场景' }} ·
            角色 {{ detail.roles_snapshot || '—' }}
          </p>
          <p v-if="detailError" class="pr__error">{{ detailError }}</p>
          <template v-else>
            <h4 class="pr-modal__label">提示词</h4>
            <pre class="pr-modal__body">{{ detailLoading ? '读取中…' : detail.prompt_text || '（空）' }}</pre>
            <template v-if="detail.answer_text">
              <h4 class="pr-modal__label">回复</h4>
              <pre class="pr-modal__body">{{ detail.answer_text }}</pre>
            </template>
            <p v-if="detail.refused" class="pr-modal__flag">该次调用被安全策略拒答</p>
          </template>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.pr {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pr__head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}

.pr__hint {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.pr__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
}

.pr__filters .yg2-input {
  width: 160px;
  height: 34px;
  padding: 0 12px;
}

.pr__days {
  width: 110px;
}

.pr__error {
  margin: 0;
  font-size: 13px;
  color: var(--yg2-cat-school);
}

.pr__table-wrap {
  overflow-x: auto;
}

.pr__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.pr__table th {
  padding: 8px 10px;
  text-align: left;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
  border-bottom: 1px solid var(--yg2-line);
}

.pr__table td {
  padding: 10px;
  border-bottom: 1px solid var(--yg2-sep);
}

.pr__table tbody tr {
  cursor: pointer;
  transition: background var(--yg2-dur-1) var(--yg2-ease-out);
}

.pr__table tbody tr:hover {
  background: var(--yg2-surface-sunken);
}

.pr__num {
  font-variant-numeric: tabular-nums;
}

.pr__empty {
  padding: 28px 10px;
  text-align: center;
  color: var(--yg2-text-secondary);
}

.pr__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.pr__pager {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pr-modal {
  position: fixed;
  inset: 0;
  z-index: 2600;
  display: grid;
  place-items: center;
  padding: 24px;
  background: color-mix(in srgb, #000 32%, transparent);
  backdrop-filter: blur(6px);
}

.pr-modal__card {
  width: min(720px, 100%);
  max-height: 82dvh;
  overflow: auto;
  padding: 20px 22px;
  border-radius: var(--yg2-card-r);
  background: var(--yg2-surface);
  box-shadow: 0 30px 80px -24px color-mix(in srgb, #000 45%, transparent);
}

.pr-modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pr-modal__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.pr-modal__meta {
  margin: 6px 0 14px;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.pr-modal__label {
  margin: 14px 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.pr-modal__body {
  margin: 0;
  padding: 12px 14px;
  border-radius: var(--yg2-r-12);
  background: var(--yg2-surface-sunken);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.pr-modal__flag {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--yg2-cat-school);
}
</style>

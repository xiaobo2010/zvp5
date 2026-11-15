<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from '@/plugins/axios'
import { getUserQuota, setUserQuota, type UserQuotaView } from '@/api/next/system/userQuotas'
import { errorMessage } from '@/utils/errorMessage'
import type { Response } from '@/api/next/types-compat'

/**
 * 用户配额：按学号/姓名搜人 → 看该用户的覆盖值与实际生效值 → 改覆盖或清空。
 * 空值表示「继承全局」，不写 0。
 */
interface PickedUser {
  oid: string
  id: string
  name: string
}

const keyword = ref('')
const searching = ref(false)
const candidates = ref<PickedUser[]>([])
const picked = ref<PickedUser | null>(null)
const quota = ref<UserQuotaView | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const files = ref<string>('')
const mb = ref<string>('')

function mbToBytes(value: string): number | null {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.round(n * 1024 * 1024) : null
}

function bytesToMb(value: number | null): string {
  if (value === null || value === undefined) {
    return ''
  }
  return String(Math.round((value / 1024 / 1024) * 10) / 10)
}

async function search() {
  const q = keyword.value.trim()
  if (!q) {
    candidates.value = []
    return
  }
  searching.value = true
  try {
    const result = (
      await axios('/users', { method: 'get', params: { query: q, page: 1, perpage: 8 } })
    ).data as Response<PickedUser[]>
    candidates.value = (result.data || []).map((u) => ({
      oid: String((u as unknown as { _id?: string })._id ?? u.oid ?? ''),
      id: u.id,
      name: u.name
    }))
  } catch (e) {
    error.value = errorMessage(e, '搜索失败')
  } finally {
    searching.value = false
  }
}

async function pick(user: PickedUser) {
  picked.value = user
  candidates.value = []
  keyword.value = `${user.name}（${user.id}）`
  loading.value = true
  error.value = ''
  try {
    quota.value = await getUserQuota(user.oid)
    files.value = quota.value.daily_max_files === null ? '' : String(quota.value.daily_max_files)
    mb.value = bytesToMb(quota.value.daily_max_bytes)
  } catch (e) {
    error.value = errorMessage(e, '读取配额失败')
    quota.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!picked.value) {
    return
  }
  saving.value = true
  error.value = ''
  try {
    quota.value = await setUserQuota(picked.value.oid, {
      daily_max_files: files.value.trim() === '' ? null : Number(files.value),
      daily_max_bytes: mbToBytes(mb.value)
    })
    files.value = quota.value.daily_max_files === null ? '' : String(quota.value.daily_max_files)
    mb.value = bytesToMb(quota.value.daily_max_bytes)
    ElMessage.success('已保存')
  } catch (e) {
    error.value = errorMessage(e, '保存失败')
  } finally {
    saving.value = false
  }
}

function clearOverride() {
  files.value = ''
  mb.value = ''
  void save()
}
</script>

<template>
  <div class="uq">
    <header class="uq__head">
      <div>
        <h3 class="uq__title">用户配额</h3>
        <p class="uq__hint">留空表示继承全局设置；填数字才是这个人的专属上限。</p>
      </div>
    </header>

    <div class="yg2-toolbar">
      <input
        v-model="keyword"
        class="yg2-input yg2-toolbar__grow"
        placeholder="搜学号或姓名"
        @keydown.enter.prevent="search"
      />
      <button class="yg2-btn yg2-btn--secondary" type="button" :disabled="searching" @click="search">
        {{ searching ? '搜索中…' : '搜索' }}
      </button>
    </div>

    <ul v-if="candidates.length" class="uq__candidates">
      <li v-for="user in candidates" :key="user.oid">
        <button type="button" class="uq__candidate" @click="pick(user)">
          <strong>{{ user.name }}</strong>
          <span>{{ user.id }}</span>
        </button>
      </li>
    </ul>

    <p v-if="error" class="uq__error">{{ error }}</p>

    <div v-if="picked" class="uq__form">
      <div class="uq__row">
        <span class="uq__label">每日文件数上限</span>
        <input v-model="files" class="yg2-input" type="number" min="1" placeholder="继承全局" />
        <span class="uq__eff">生效 {{ quota?.effective.daily_max_files ?? '—' }}</span>
      </div>
      <div class="uq__row">
        <span class="uq__label">每日容量上限（MB）</span>
        <input v-model="mb" class="yg2-input" type="number" min="1" step="0.5" placeholder="继承全局" />
        <span class="uq__eff">
          生效 {{ quota ? Math.round(quota.effective.daily_max_bytes / 1024 / 1024) : '—' }} MB
        </span>
      </div>
      <div class="uq__actions">
        <button class="yg2-btn yg2-btn--primary" type="button" :disabled="saving || loading" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button class="yg2-btn yg2-btn--quiet" type="button" :disabled="saving" @click="clearOverride">
          恢复继承
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.uq {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.uq__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.uq__hint {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.uq__candidates {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.uq__candidate {
  display: flex;
  gap: 10px;
  align-items: baseline;
  width: 100%;
  padding: 8px 10px;
  border-radius: 10px;
  text-align: left;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.uq__candidate:hover {
  background: var(--yg2-act-hover);
}

.uq__candidate span {
  font-size: 12px;
  color: var(--yg2-text-secondary);
  font-variant-numeric: tabular-nums;
}

.uq__error {
  margin: 0;
  color: var(--yg2-danger);
  font-size: 12.5px;
}

.uq__form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: var(--yg2-r-16);
  background: var(--yg2-surface-sunken);
}

.uq__row {
  display: grid;
  grid-template-columns: 140px minmax(0, 200px) 1fr;
  align-items: center;
  gap: 10px;
}

.uq__label {
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.uq__eff {
  font-size: 12px;
  color: var(--yg2-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.uq__actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}

@media (max-width: 768px) {
  .uq__row {
    grid-template-columns: 1fr;
  }
}
</style>

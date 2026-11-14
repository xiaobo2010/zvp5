<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ROLE_FEATURE_KEYS,
  getMyRolePolicy,
  getRolePolicies,
  updateRolePolicy,
  type MyRolePolicy,
  type RolePolicy
} from '@/api/next/system/rolePolicies'
import { errorMessage } from '@/utils/errorMessage'

/**
 * 角色策略矩阵：13 个功能开关 × 可写角色 + 两类月度配额。
 *
 * 写接口要求另一位管理员的 6 位动态码（双人复核），所以「保存」会先弹
 * 一个动态码输入框，拿到码才发 PUT；成功后用响应回填该角色，不必整页刷新。
 */
const loading = ref(true)
const loadError = ref('')
const saveError = ref('')

const roles = ref<RolePolicy[]>([])
const editableRoles = ref<string[]>([])
const featureKeys = ref<string[]>([...ROLE_FEATURE_KEYS])
const myPolicy = ref<MyRolePolicy | null>(null)

/** 本地草稿：role → 策略副本，保存成功后用它回填原数组 */
const drafts = ref<Record<string, RolePolicy>>({})

const totpOpen = ref(false)
const totpCode = ref('')
const totpRole = ref('')
const saving = ref(false)

const featureLabels: Record<string, string> = {
  'ai.chat': 'AI 对话',
  'ai.ocr': 'OCR 识别',
  'ai.agent': 'AI 代理执行',
  'ai.schedule': '定时任务',
  'ai.keys': 'AI 密钥管理',
  'activity.stats': '活动统计',
  'attachment.manage': '附件审核',
  'export.time': '导出义工时长',
  'import.activity': '导入活动成员',
  'logs.read': '查看调用日志',
  'notify.publish': '发布站内通知',
  'ban.read': '查看封禁记录',
  'quota.manage': '审批额度申请'
}

const quotaKinds = [
  { key: 'af' as const, label: 'AI 额度（次）' },
  { key: 'schedule' as const, label: '定时任务（条）' }
]

const columns = computed(() => roles.value.map((item) => item.role))

function draftOf(role: string): RolePolicy | undefined {
  return drafts.value[role]
}

function isEnabled(role: string, key: string): boolean {
  return draftOf(role)?.features?.[key] !== false
}

function toggle(role: string, key: string) {
  const draft = drafts.value[role]
  if (!draft) {
    return
  }
  draft.features = { ...draft.features, [key]: !isEnabled(role, key) }
}

function quotaValue(role: string, kind: 'af' | 'schedule'): string {
  const value = draftOf(role)?.quotas?.[kind]
  return value === null || value === undefined ? '' : String(value)
}

function setQuota(role: string, kind: 'af' | 'schedule', raw: string) {
  const draft = drafts.value[role]
  if (!draft) {
    return
  }
  const trimmed = raw.trim()
  draft.quotas = {
    ...draft.quotas,
    [kind]: trimmed === '' ? null : Math.max(0, Number.parseInt(trimmed, 10) || 0)
  }
}

function isDirty(role: string): boolean {
  const draft = drafts.value[role]
  const source = roles.value.find((item) => item.role === role)
  if (!draft || !source) {
    return false
  }
  return JSON.stringify(draft.features) !== JSON.stringify(source.features) ||
    JSON.stringify(draft.quotas) !== JSON.stringify(source.quotas)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await getRolePolicies()
    roles.value = data.roles ?? []
    editableRoles.value = data.editable_roles ?? []
    featureKeys.value = data.feature_keys?.length ? data.feature_keys : [...ROLE_FEATURE_KEYS]
    drafts.value = Object.fromEntries(
      roles.value.map((item) => [
        item.role,
        { ...item, features: { ...item.features }, quotas: { ...item.quotas } }
      ])
    )
    myPolicy.value = await getMyRolePolicy().catch(() => null)
  } catch (error) {
    loadError.value = errorMessage(error, '读取角色策略失败')
  } finally {
    loading.value = false
  }
}

function askTotp(role: string) {
  if (!editableRoles.value.includes(role)) {
    return
  }
  saveError.value = ''
  totpRole.value = role
  totpCode.value = ''
  totpOpen.value = true
}

async function confirmSave() {
  const role = totpRole.value
  const draft = drafts.value[role]
  const code = totpCode.value.trim()
  if (!draft || code.length !== 6) {
    saveError.value = '请输入 6 位动态码'
    return
  }
  saving.value = true
  saveError.value = ''
  try {
    const updated = await updateRolePolicy(
      role,
      { features: draft.features, quotas: draft.quotas },
      code
    )
    const index = roles.value.findIndex((item) => item.role === role)
    if (index >= 0) {
      roles.value[index] = updated
    }
    drafts.value[role] = { ...updated, features: { ...updated.features }, quotas: { ...updated.quotas } }
    totpOpen.value = false
  } catch (error) {
    saveError.value = errorMessage(error, '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="rp">
    <header class="rp__head">
      <div>
        <h2 class="rp__title">角色策略</h2>
        <p class="rp__desc">
          功能开关与月度配额按角色下发；admin 固定全量权限，不在此表内。保存需要另一位管理员的动态码。
        </p>
      </div>
      <button class="yg2-btn yg2-btn--secondary yg2-btn--sm" type="button" :disabled="loading" @click="load">
        刷新
      </button>
    </header>

    <p v-if="loadError" class="rp__error">{{ loadError }}</p>

    <div v-if="loading" class="rp__skeleton">
      <span v-for="n in 6" :key="n" class="yg2-skeleton" style="height: 28px" />
    </div>

    <div v-else class="rp__scroll">
      <table class="rp__table">
        <thead>
          <tr>
            <th class="rp__th-feature">功能</th>
            <th v-for="role in columns" :key="role" class="rp__th-role">
              <span>{{ role }}</span>
              <button
                v-if="editableRoles.includes(role)"
                class="rp__save"
                type="button"
                :disabled="!isDirty(role)"
                @click="askTotp(role)"
              >
                保存
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="key in featureKeys" :key="key">
            <th class="rp__th-feature">{{ featureLabels[key] ?? key }}</th>
            <td v-for="role in columns" :key="role" class="rp__td">
              <button
                class="rp__switch"
                :class="{ 'is-on': isEnabled(role, key) }"
                type="button"
                :disabled="!editableRoles.includes(role)"
                :aria-pressed="isEnabled(role, key)"
                @click="toggle(role, key)"
              >
                <span class="rp__switch-dot" />
              </button>
            </td>
          </tr>
          <tr v-for="kind in quotaKinds" :key="kind.key" class="rp__quota-row">
            <th class="rp__th-feature">{{ kind.label }}</th>
            <td v-for="role in columns" :key="role" class="rp__td">
              <input
                class="rp__input"
                type="number"
                min="0"
                inputmode="numeric"
                placeholder="默认"
                :value="quotaValue(role, kind.key)"
                :disabled="!editableRoles.includes(role)"
                @input="setQuota(role, kind.key, ($event.target as HTMLInputElement).value)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="myPolicy" class="rp__mine">
      <h3 class="rp__mine-title">我的生效策略</h3>
      <p class="rp__mine-desc">
        由你当前所有角色合并而来，实时生效；下面是这次刷新时的快照。
      </p>
      <div class="rp__mine-grid">
        <span
          v-for="key in featureKeys"
          :key="key"
          class="rp__chip"
          :class="{ 'is-off': myPolicy.features?.[key] === false }"
        >
          {{ featureLabels[key] ?? key }}
        </span>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="totpOpen" class="rp-modal" @click.self="totpOpen = false">
        <div class="rp-modal__card">
          <h3 class="rp-modal__title">保存「{{ totpRole }}」的策略</h3>
          <p class="rp-modal__desc">需要另一位管理员当前的 6 位动态码。</p>
          <input
            v-model="totpCode"
            class="rp-modal__input"
            inputmode="numeric"
            maxlength="6"
            placeholder="000000"
            autofocus
          />
          <p v-if="saveError" class="rp-modal__error">{{ saveError }}</p>
          <div class="rp-modal__foot">
            <button class="yg2-btn yg2-btn--quiet" type="button" @click="totpOpen = false">取消</button>
            <button class="yg2-btn yg2-btn--primary" type="button" :disabled="saving" @click="confirmSave">
              {{ saving ? '保存中…' : '确认保存' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.rp {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.rp__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.rp__title {
  margin: 0 0 4px;
  font-size: 17px;
  font-weight: 600;
}

.rp__desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.rp__error {
  margin: 0;
  color: var(--yg2-danger);
  font-size: 13px;
}

.rp__skeleton {
  display: grid;
  gap: 8px;
}

.rp__scroll {
  overflow-x: auto;
}

.rp__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.rp__table th,
.rp__table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--yg2-sep);
  text-align: left;
  white-space: nowrap;
}

.rp__th-feature {
  width: 180px;
  font-weight: 500;
  color: var(--yg2-text-secondary);
}

.rp__th-role {
  font-weight: 600;
  color: var(--yg2-text);
}

.rp__save {
  margin-left: 8px;
  padding: 2px 8px;
  border: 0;
  border-radius: 999px;
  background: var(--yg2-accent-soft);
  color: var(--yg2-accent);
  font-size: 11.5px;
  cursor: pointer;
}

.rp__save:disabled {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-tertiary);
  cursor: default;
}

.rp__td {
  text-align: center;
}

.rp__switch {
  position: relative;
  width: 36px;
  height: 20px;
  border: 0;
  border-radius: 999px;
  background: var(--yg2-surface-sunken);
  box-shadow: inset 0 0 0 1px var(--yg2-line);
  cursor: pointer;
  transition: background 160ms var(--yg2-ease-out);
}

.rp__switch.is-on {
  background: var(--yg2-accent);
  box-shadow: none;
}

.rp__switch:disabled {
  opacity: 0.5;
  cursor: default;
}

.rp__switch-dot {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgb(0 0 0 / 25%);
  transition: transform 180ms var(--yg2-ease-out);
}

.rp__switch.is-on .rp__switch-dot {
  transform: translateX(16px);
}

.rp__input {
  width: 92px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--yg2-line);
  border-radius: 8px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
  font: inherit;
  font-size: 12.5px;
  text-align: center;
}

.rp__mine {
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--yg2-sep);
}

.rp__mine-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
}

.rp__mine-desc {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.rp__mine-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.rp__chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--yg2-accent-soft);
  color: var(--yg2-accent);
  font-size: 12px;
}

.rp__chip.is-off {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-tertiary);
  text-decoration: line-through;
}

.rp-modal {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  background: rgb(0 0 0 / 35%);
  backdrop-filter: blur(6px);
}

.rp-modal__card {
  width: min(380px, calc(100vw - 32px));
  padding: 22px;
  border-radius: var(--yg2-r-20);
  background: var(--yg2-surface);
  box-shadow: var(--yg2-shadow-float, 0 24px 64px rgb(0 0 0 / 22%));
}

.rp-modal__title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
}

.rp-modal__desc {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.rp-modal__input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--yg2-line);
  border-radius: 12px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
  font: inherit;
  font-size: 18px;
  letter-spacing: 0.4em;
  text-align: center;
}

.rp-modal__error {
  margin: 8px 0 0;
  color: var(--yg2-danger);
  font-size: 12.5px;
}

.rp-modal__foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>

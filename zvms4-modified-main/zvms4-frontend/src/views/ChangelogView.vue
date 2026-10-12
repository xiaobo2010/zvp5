<script setup lang="ts">
import {
  ElCard,
  ElTimeline,
  ElTimelineItem,
  ElPageHeader,
  ElButton,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElDatePicker,
  ElMessage,
  ElMessageBox
} from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { useHeaderStore } from '@/stores/header'
import { useUserStore } from '@/stores/user'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ref, computed, onMounted } from 'vue'
import api from '@/api'
import { changelog, type ChangelogEntry as StaticChangelogEntry } from '@/data/changelog'
import type { ChangelogEntry } from '@/api/changelog'

const { t } = useI18n()
const router = useRouter()
const header = useHeaderStore()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))

type DisplayEntry = ChangelogEntry | StaticChangelogEntry

const entries = ref<DisplayEntry[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<string | null>(null)
const form = ref({
  version: '',
  date: '',
  items: ''
})

const dialogTitle = computed(() =>
  editingId.value ? t('changelog.actions.edit') : t('changelog.actions.create')
)

function hasId(entry: DisplayEntry): entry is ChangelogEntry {
  return 'id' in entry && typeof entry.id === 'string'
}

async function load() {
  loading.value = true
  const data = await api.changelog.getChangelog()
  entries.value = data ?? changelog.slice()
  loading.value = false
}

onMounted(load)

function resetForm() {
  form.value = {
    version: '',
    date: '',
    items: ''
  }
  editingId.value = null
}

function startCreate() {
  resetForm()
  dialogVisible.value = true
}

function startEdit(entry: ChangelogEntry) {
  editingId.value = entry.id
  form.value = {
    version: entry.version,
    date: entry.date,
    items: entry.items.join('\n')
  }
  dialogVisible.value = true
}

function parseItems(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

async function save() {
  if (!form.value.version || !form.value.date) return
  const payload = {
    version: form.value.version,
    date: form.value.date,
    items: parseItems(form.value.items)
  }
  saving.value = true
  try {
    if (editingId.value) {
      await api.changelog.updateChangelog(editingId.value, payload)
    } else {
      await api.changelog.createChangelog(payload)
    }
    ElMessage.success(t('changelog.success.saved'))
    dialogVisible.value = false
    await load()
  } catch {
    ElMessage.error(t('changelog.error.saveFailed'))
  } finally {
    saving.value = false
  }
}

async function remove(entry: DisplayEntry) {
  if (!hasId(entry)) return
  try {
    await ElMessageBox.confirm(
      `${t('changelog.deleteConfirm.message')}（${entry.version}）`,
      t('changelog.deleteConfirm.title'),
      {
        confirmButtonText: t('changelog.actions.delete'),
        cancelButtonText: t('changelog.actions.cancel'),
        type: 'warning'
      }
    )
  } catch {
    return
  }
  loading.value = true
  try {
    await api.changelog.deleteChangelog(entry.id)
    ElMessage.success(t('changelog.success.deleted'))
    await load()
  } finally {
    loading.value = false
  }
}

header.setHeader(t('nav.changelog'))
</script>

<template>
  <div class="p-4">
    <ElPageHeader :icon="ArrowLeft" @back="router.back()" class="py-4">
      <template #content>
        <h1 class="text-xl">{{ t('nav.changelog') }}</h1>
      </template>
      <template #extra>
        <ElButton
          v-if="isAdmin"
          type="primary"
          text
          bg
          @click="startCreate"
        >
          {{ t('changelog.actions.create') }}
        </ElButton>
      </template>
    </ElPageHeader>
    <ElCard shadow="never" v-loading="loading">
      <ElTimeline>
        <ElTimelineItem
          v-for="entry in entries"
          :key="hasId(entry) ? entry.id : entry.version"
          :timestamp="entry.date"
          placement="top"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <h3 class="text-lg font-bold mb-2">{{ entry.version }}</h3>
              <ul class="list-disc pl-5 text-sm space-y-1">
                <li v-for="(item, idx) in entry.items" :key="idx">{{ item }}</li>
              </ul>
            </div>
            <div v-if="isAdmin && hasId(entry)" class="flex-shrink-0 space-x-2">
              <ElButton
                type="primary"
                size="small"
                text
                bg
                @click="startEdit(entry)"
              >
                {{ t('changelog.actions.edit') }}
              </ElButton>
              <ElButton
                type="danger"
                size="small"
                text
                bg
                @click="remove(entry)"
              >
                {{ t('changelog.actions.delete') }}
              </ElButton>
            </div>
          </div>
        </ElTimelineItem>
      </ElTimeline>
    </ElCard>

    <ElDialog v-model="dialogVisible" :title="dialogTitle" width="520px">
      <ElForm label-position="right" label-width="96px">
        <ElFormItem :label="t('changelog.fields.version')" required>
          <ElInput v-model="form.version" />
        </ElFormItem>
        <ElFormItem :label="t('changelog.fields.date')" required>
          <ElDatePicker
            v-model="form.date"
            type="date"
            value-format="YYYY-MM-DD"
            class="w-full"
          />
        </ElFormItem>
        <ElFormItem :label="t('changelog.fields.items')">
          <ElInput
            v-model="form.items"
            type="textarea"
            :rows="6"
            :placeholder="t('changelog.fields.itemsHint')"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">{{ t('changelog.actions.cancel') }}</ElButton>
        <ElButton
          type="primary"
          @click="save"
          :loading="saving"
          :disabled="!form.version || !form.date"
        >
          {{ t('changelog.actions.save') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

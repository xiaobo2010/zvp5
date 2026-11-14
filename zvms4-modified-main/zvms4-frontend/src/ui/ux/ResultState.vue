<script setup lang="ts">
import StateBlock from '@/ui/ux/StateBlock.vue'

/**
 * 结果页：成功给大对勾 + 下一步；失败写清原因 + 返回修改。
 * 用法：<ResultState :ok="ok" :reason="reason" @next="..." @retry="..." />
 */
withDefaults(
  defineProps<{
    ok: boolean
    title?: string
    reason?: string
    nextText?: string
    retryText?: string
  }>(),
  { title: '', reason: '', nextText: '完成', retryText: '返回修改' }
)

const emit = defineEmits<{ next: []; retry: [] }>()
</script>

<template>
  <StateBlock
    :tone="ok ? 'success' : 'error'"
    :title="title || (ok ? '提交成功' : '提交失败')"
    :description="ok ? (reason || '我们已经记录这次操作。') : reason"
  >
    <template #actions>
      <button v-if="ok" class="yg2-btn yg2-btn--primary" type="button" @click="emit('next')">
        {{ nextText }}
      </button>
      <template v-else>
        <button class="yg2-btn yg2-btn--primary" type="button" @click="emit('retry')">
          {{ retryText }}
        </button>
      </template>
    </template>
  </StateBlock>
</template>

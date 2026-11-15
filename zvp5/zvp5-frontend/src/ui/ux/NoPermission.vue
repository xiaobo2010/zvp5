<script setup lang="ts">
import StateBlock from '@/ui/ux/StateBlock.vue'

/**
 * 无权限提示：说清需要什么权限、找谁开通，并给一个可直接点的申请入口。
 * 申请入口默认落地到「关于」页的联系方式，可由调用方用 apply-href 覆盖。
 */
const props = withDefaults(
  defineProps<{
    title?: string
    description?: string
    contact?: string
    applyHref?: string
  }>(),
  {
    title: '这里需要更高的权限',
    description: '',
    contact: '平台管理员（高二五班 翁振轩）',
    applyHref: '/about'
  }
)
</script>

<template>
  <StateBlock
    title="这里需要更高的权限"
    :description="
      props.description ||
      `你的账号还没有开通这项权限，可以联系 ${props.contact} 申请，开通后原页面就能直接进入。`
    "
  >
    <template #actions>
      <a class="yg2-btn yg2-btn--primary" :href="props.applyHref">申请权限</a>
    </template>
  </StateBlock>
</template>

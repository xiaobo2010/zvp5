<script lang="ts" setup>
import { useI18n } from 'vue-i18n'
import { ElButton, ElTooltip } from 'element-plus'
import { computed, toRefs, type Component as VueComponent } from 'vue'
import { useWindowSize } from '@vueuse/core'
import { Remove } from '@element-plus/icons-vue'

const { t } = useI18n()
const { width, height } = useWindowSize()

const props = withDefaults(
  defineProps<{
    size?: 'large' | 'default' | 'small'
    type?: 'primary' | 'warning' | 'success' | 'danger' | 'info' | ''
    icon?: VueComponent
    unknown?: boolean
    force?: 'full' | 'short'
    onlyDisplayIcon?: boolean
    bg?: boolean
    /** 自定义色系，用于与语义色（primary/success/warning…）区隔的场景 */
    tone?: 'cyan' | 'violet' | 'magenta'
  }>(),
  {
    size: 'small',
    type: '',
    bg: true
  }
)

const { size, type, icon, unknown, force, onlyDisplayIcon, tone } = toRefs(props)

const toneClass = computed(() => (tone?.value ? `tag-tone--${tone.value}` : undefined))
</script>

<template>
  <ElButton
    v-if="(!unknown && width > height * 1.5 && force !== 'short') || force === 'full'"
    :icon="icon"
    :type="type"
    :size="size ?? 'small'"
    :class="toneClass"
    text
    :bg="bg"
    round
  >
    <slot></slot>
  </ElButton>
  <ElTooltip v-else-if="!unknown || force === 'short'" :effect="'light'">
    <template #content>
      <slot></slot>
    </template>
    <ElButton
      :icon="icon"
      :type="type"
      :size="size ?? 'small'"
      :class="toneClass"
      :circle="!onlyDisplayIcon"
      :round="onlyDisplayIcon"
      text
      :bg="bg"
    />
  </ElTooltip>
  <ElButton
    v-else-if="!onlyDisplayIcon"
    type="danger"
    :icon="Remove"
    :size="size ?? 'small'"
    text
    :bg="bg"
  >
    {{ t('activity.type.unknown') }}
  </ElButton>
  <ElButton v-else type="danger" :icon="Remove" :size="size ?? 'small'" text :bg="bg" />
</template>

<style scoped>
/* 自定义色系：与语义状态色（蓝/绿/红）明显区隔，深色用亮字软底。 */
.el-button.tag-tone--cyan {
  --el-button-text-color: #67e8f9;
  --el-button-hover-text-color: #a5f3fc;
  --el-button-active-text-color: #cffafe;
  --el-button-bg-color: rgba(6, 182, 212, 0.18);
  --el-button-hover-bg-color: rgba(6, 182, 212, 0.28);
  --el-button-active-bg-color: rgba(6, 182, 212, 0.36);
  --el-button-border-color: transparent;
}

.el-button.tag-tone--violet {
  --el-button-text-color: #c4b5fd;
  --el-button-hover-text-color: #ddd6fe;
  --el-button-active-text-color: #ede9fe;
  --el-button-bg-color: rgba(139, 92, 246, 0.2);
  --el-button-hover-bg-color: rgba(139, 92, 246, 0.3);
  --el-button-active-bg-color: rgba(139, 92, 246, 0.38);
  --el-button-border-color: transparent;
}

.el-button.tag-tone--magenta {
  --el-button-text-color: #f9a8d4;
  --el-button-hover-text-color: #fbcfe8;
  --el-button-active-text-color: #fce7f3;
  --el-button-bg-color: rgba(236, 72, 153, 0.2);
  --el-button-hover-bg-color: rgba(236, 72, 153, 0.3);
  --el-button-active-bg-color: rgba(236, 72, 153, 0.38);
  --el-button-border-color: transparent;
}

/* 浅色主题：软底 + 深字，保证 ≥4.5:1 对比度。 */
html[data-theme='light'] .el-button.tag-tone--cyan {
  --el-button-text-color: #0e7490;
  --el-button-hover-text-color: #155e75;
  --el-button-active-text-color: #164e63;
  --el-button-bg-color: rgba(6, 182, 212, 0.12);
  --el-button-hover-bg-color: rgba(6, 182, 212, 0.18);
  --el-button-active-bg-color: rgba(6, 182, 212, 0.24);
}

html[data-theme='light'] .el-button.tag-tone--violet {
  --el-button-text-color: #6d28d9;
  --el-button-hover-text-color: #5b21b6;
  --el-button-active-text-color: #4c1d95;
  --el-button-bg-color: rgba(139, 92, 246, 0.12);
  --el-button-hover-bg-color: rgba(139, 92, 246, 0.18);
  --el-button-active-bg-color: rgba(139, 92, 246, 0.24);
}

html[data-theme='light'] .el-button.tag-tone--magenta {
  --el-button-text-color: #be185d;
  --el-button-hover-text-color: #9d174d;
  --el-button-active-text-color: #831843;
  --el-button-bg-color: rgba(236, 72, 153, 0.12);
  --el-button-hover-bg-color: rgba(236, 72, 153, 0.18);
  --el-button-active-bg-color: rgba(236, 72, 153, 0.24);
}
</style>

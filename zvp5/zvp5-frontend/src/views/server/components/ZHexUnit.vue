<script setup lang="ts">
/**
 * MAGI 六边形消息单元。
 *
 * EVA MAGI 的状态面板由一块块六边形「消息单元」拼成：每块自己带一个
 * 状态读数与一行说明。这里把那块单元抽成组件，服务器台各分区共用：
 *   - 六边形徽章承载最重要的那一个数（ON/OFF、连接池占用、剩余秒数…）
 *   - 右侧 HUD mono 标签给出通道名（SYS // DB），说明文字给出上下文
 *
 * 徽章用 clip-path 切出，不依赖图片；tone 决定强调色，danger 态会呼吸。
 */
withDefaults(
  defineProps<{
    /** 通道标签，如 'SYS // DB' */
    label?: string
    /** 徽章里的主读数（短文本或数字） */
    value?: string | number
    /** 说明文字 */
    hint?: string
    /** 语气：accent 正常 / ok 良好 / warn 警告 / danger 危险 / idle 空闲 */
    tone?: 'accent' | 'ok' | 'warn' | 'danger' | 'idle'
    /** 徽章尺寸 */
    size?: 'sm' | 'md' | 'lg'
    /** 说明文字是否降噪（次要信息用 muted） */
    muted?: boolean
  }>(),
  {
    label: '',
    value: '',
    hint: '',
    tone: 'accent',
    size: 'md',
    muted: false
  }
)
</script>

<template>
  <div class="phi-srv-unit">
    <span class="phi-srv-hex" :class="[`phi-srv-hex--${tone}`, `phi-srv-hex--${size}`]">
      <span class="phi-srv-hex__value">{{ value }}</span>
    </span>
    <span class="phi-srv-unit__body">
      <span v-if="label" class="phi-srv-unit__label">{{ label }}</span>
      <span v-if="hint" class="phi-srv-unit__hint" :class="{ 'phi-srv-unit__hint--muted': muted }">
        {{ hint }}
      </span>
    </span>
  </div>
</template>

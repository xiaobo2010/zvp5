<script setup lang="ts">
import { ref } from 'vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { palettes, palettePreference, setPalettePreference, type PalettePreference } from '@/composables/appearance'

const saved = ref<boolean | null>(null)

function choose(value: PalettePreference) {
  saved.value = setPalettePreference(value)
}
</script>

<template>
  <fieldset id="appearance" class="appearance">
    <legend>外观设置</legend>
    <p class="appearance__description">背景、卡片和导航会随配色一起变化。选择保存在此浏览器。</p>
    <div class="appearance__options">
      <label
        v-for="palette in palettes"
        :key="palette.id"
        class="appearance__option"
        :class="`palette-${palette.id}`"
      >
        <input type="radio" name="appearance" :value="palette.id" :checked="palettePreference === palette.id" @change="choose(palette.id)" />
        <span class="appearance__swatch" aria-hidden="true"></span>
        <span>{{ palette.label }}</span>
        <small>{{ palette.description }}</small>
      </label>
    </div>
    <label class="appearance__auto">
      <input type="radio" name="appearance" value="auto" :checked="palettePreference === 'auto'" @change="choose('auto')" />
      随本地时间自动切换
    </label>
    <div aria-live="polite">
      <BubbleFeedback
        v-if="saved !== null"
        :key="palettePreference"
        :class="saved ? 'glass-feedback--success' : 'glass-feedback--warn'"
      >
        {{ saved ? '配色已应用。' : '配色已应用于此页面。浏览器无法保存设置。' }}
      </BubbleFeedback>
    </div>
  </fieldset>
</template>

<style scoped>
.appearance {
  min-inline-size: 0;
  padding: clamp(16px, 4vw, 28px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: var(--surface-matte);
  box-shadow: var(--glass-shadow);
}

.appearance legend {
  padding-inline: 8px;
  color: var(--color-text-primary);
  font-size: var(--font-size-title-3);
  font-weight: 650;
}

.appearance__description {
  margin-block-end: var(--space-4);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
}

.appearance__options {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.appearance__option {
  position: relative;
  display: grid;
  gap: 2px;
  padding: var(--space-2);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
}

.appearance__option:has(:checked) {
  border-color: var(--accent-ink);
  box-shadow: inset 0 0 0 1px var(--accent-ink);
}

.appearance__option input {
  position: absolute;
  inset-block-start: 16px;
  inset-inline-end: 16px;
}

.appearance input {
  accent-color: var(--accent-ink);
}

.appearance__swatch {
  min-height: 42px;
  border-radius: var(--radius-sm);
  background: linear-gradient(125deg, var(--palette-main), var(--palette-low) 52%, var(--palette-edge));
}

.appearance__option small {
  color: var(--color-text-secondary);
}

.appearance__auto {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 44px;
  width: fit-content;
  cursor: pointer;
}

@media (max-width: 540px) {
  .appearance__options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

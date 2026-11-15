import pluginCypress from 'eslint-plugin-cypress'
import pluginVue from 'eslint-plugin-vue'
import {
  defineConfigWithVueTs,
  vueTsConfigs
} from '@vue/eslint-config-typescript'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default defineConfigWithVueTs(
  {
    name: 'zvms/files-to-lint',
    files: ['**/*.{js,mjs,cjs,jsx,ts,mts,cts,tsx,vue}']
  },
  {
    name: 'zvms/ignores',
    ignores: [
      '**/dist/**',
      '**/coverage/**',
      '**/node_modules/**',
      '**/*.d.ts'
    ]
  },
  pluginVue.configs['flat/essential'],
  vueTsConfigs.base,
  {
    name: 'zvms/existing-code-compatibility',
    rules: {
      'vue/no-v-text-v-html-on-component': 'warn'
    }
  },
  {
    // 设计系统基础件（src/ui/**）按产品语义取单词名（Segmented / Skeleton / …），
    // 与业务组件的 Z 前缀多词命名并存；只对该目录放开多词校验，不影响其他目录。
    name: 'zvms/ui-single-word-components',
    files: ['src/ui/**/*.vue'],
    rules: {
      'vue/multi-word-component-names': 'off'
    }
  },
  {
    name: 'zvms/cypress',
    files: ['cypress/**/*.{js,ts,cy,cy.ts}'],
    ...pluginCypress.configs.recommended
  },
  skipFormatting
)

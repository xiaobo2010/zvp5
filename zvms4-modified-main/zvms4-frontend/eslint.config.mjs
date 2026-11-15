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
      '**/.vite/**',
      // 作者本机 macOS Finder 生成的「副本」文件（文件名带 " 2"），
      // 与同名原文件重复且代码里没有引用，不参与 lint。
      '**/* 2.*',
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
    name: 'zvms/cypress',
    files: ['cypress/**/*.{js,ts,cy,cy.ts}'],
    ...pluginCypress.configs.recommended
  },
  skipFormatting
)

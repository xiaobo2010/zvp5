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
    name: 'zvms/cypress',
    files: ['cypress/**/*.{js,ts,cy,cy.ts}'],
    ...pluginCypress.configs.recommended
  },
  skipFormatting
)

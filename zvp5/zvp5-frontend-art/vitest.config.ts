import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import { viteBaseConfig } from './vite.config'

export default mergeConfig(
  viteBaseConfig,
  defineConfig({
    resolve: {
      alias: [
        // 源码与模板组件在构建期改用 element-plus 深路径 import 以避免
        // barrel 把全部组件提升进首屏 chunk；测试不关心打包形态，而
        // 大量 spec 的 vi.mock('element-plus') 依赖「所有 import 落到同一
        // 模块 id」才能连深路径一起拦截。这里把深路径统一指回 barrel，
        // 让 mock 语义保持改动前原样。
        {
          find: /^element-plus\/es\/components\/[^/]+\/index\.mjs$/,
          replacement: 'element-plus'
        }
      ]
    },
    test: {
      environment: 'jsdom',
      exclude: [...configDefaults.exclude, 'e2e/*'],
      root: fileURLToPath(new URL('./', import.meta.url))
    }
  })
)

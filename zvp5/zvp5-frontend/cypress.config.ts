import { defineConfig } from 'cypress'

/**
 * 发行版验收的产物一律落在 /tmp/opencode，避免截图/视频进仓库。
 *
 * baseUrl 默认仍是本地 preview；Agent 验收用同源 mock server 时通过
 * `--config baseUrl=http://127.0.0.1:4199` 覆盖（见 cypress/mock/agent-mock-server.mjs）。
 */
const screenshotsFolder =
  process.env.E2E_SCREENSHOTS_DIR || '/tmp/opencode/zvp5-agent-e2e/screenshots'
const videosFolder = process.env.E2E_VIDEOS_DIR || '/tmp/opencode/zvp5-agent-e2e/videos'
const downloadsFolder =
  process.env.E2E_DOWNLOADS_DIR || '/tmp/opencode/zvp5-agent-e2e/downloads'

export default defineConfig({
  e2e: {
    specPattern: 'cypress/e2e/**/*.{cy,spec}.{js,jsx,ts,tsx}',
    baseUrl: 'http://localhost:4173',
    supportFile: 'cypress/support/e2e.ts',
    screenshotsFolder,
    videosFolder,
    downloadsFolder,
    video: false,
    screenshotOnRunFailure: true,
    viewportWidth: 1280,
    viewportHeight: 900,
    setupNodeEvents(on) {
      on('before:browser:launch', (_browser, launchOptions) => {
        // Chrome 无头窗口默认 1280x720，会把 >720 高的视口截图裁掉；
        // 放大窗口后 cy.viewport(2000x1200 / 1200x2000) 才能 1:1 落图。
        launchOptions.args.push('--window-size=2600,2400')
        return launchOptions
      })
    }
  }
})

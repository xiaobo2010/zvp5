/**
 * Agent（/ai/chat）发行版验收：真实 Chromium/Cypress 驱动生产构建。
 *
 * 数据来自 cypress/mock/agent-mock-server.mjs（同源 Node mock server）：
 * - 静态资源是 `vite build` 产物，页面走真实 vue-router + AIHome + ZAiChat；
 * - /api/** 全部由 mock 应答，SSE 分片带延迟，不连真实后端、不读凭证。
 *
 * 断言覆盖：
 * 1) Markdown 正文真实渲染且终态不重复（live 文本被 terminal/snapshot 清掉）；
 * 2) 工具时间线 pending → success、名称/摘要/展开详情可用；
 * 3) composer 完整在视口内、消息区独立滚动、document 无横向溢出；
 * 4) 工具行触控高度 ≥ 44px；
 * 5) SSE fallback 时历史与输入仍可用；
 * 6) 记录 console.error / pageerror / unhandledrejection，本次不允许出现。
 *
 * 截图统一落在 /tmp/opencode/zvp5-agent-e2e（见 cypress.config.ts）。
 */

const CONVERSATION_TITLE = '发布前验收 · ZVAgent'
const TRIGGER_TEXT = '帮我巡检 /archive 目录，然后给出发布前检查结论。'
const FINAL_MARKER = 'AGENT-E2E-DONE-7F3A'
const TOOL_RESULT = '列出 2 个目录：inbox, outbox'
const TOOL_ARGS = '{"path":"/archive"}'

const USER_STATE = {
  id: 1001,
  _id: 'user-agent-e2e',
  name: 'E2E 验收管理员',
  position: ['admin'],
  groups: [],
  class_id: '',
  isLogin: true,
  shouldResetPassword: false,
  banned: false,
  bannedNoticeAt: 0,
  banReason: '',
  banUntil: 0,
  time: { socialPractice: 0, onCampus: 0, offCampus: 0 },
  language: 'zh-CN'
}

function seedSession(win: Window) {
  win.localStorage.setItem('token', 'e2e-token')
  win.localStorage.setItem('user', JSON.stringify(USER_STATE))
  // App.vue 启动时会强制把 security < 4 的会话登出；验收环境预置为已升级。
  win.localStorage.setItem('security', '4')
  // 跳过首次使用风险弹窗，避免它挡住页面（该 flag 按账号落 localStorage）。
  win.localStorage.setItem(`zvms.agent.riskAck.${USER_STATE._id}`, '1')
  win.localStorage.setItem('cached', '1')
}

/** 在页面脚本执行前挂上错误采集。 */
function installErrorCollectors(win: Window) {
  const errors = []
  win.__agentE2EErrors = errors
  const originalError = win.console.error.bind(win.console)
  win.console.error = (...args) => {
    try {
      errors.push(
        args
          .map((item) => {
            if (typeof item === 'string') return item
            if (item instanceof Error) return item.stack || item.message
            try {
              return JSON.stringify(item)
            } catch {
              return String(item)
            }
          })
          .join(' ')
      )
    } catch {
      errors.push('console.error (unserializable)')
    }
    originalError(...args)
  }
  win.addEventListener('error', (event) => {
    errors.push(`pageerror: ${event.message || String(event.error)}`)
  })
  win.addEventListener('unhandledrejection', (event) => {
    errors.push(`unhandledrejection: ${String(event.reason)}`)
  })
}

function assertNoPageErrors() {
  cy.window().then((win) => {
    const errors = win.__agentE2EErrors || []
    expect(errors, `页面错误（console.error/pageerror/unhandledrejection）`).to.deep.equal([])
  })
}

function assertNoHorizontalOverflow() {
  cy.document().then((doc) => {
    const root = doc.documentElement
    expect(root.scrollWidth, 'documentElement.scrollWidth').to.be.at.most(root.clientWidth + 1)
    expect(doc.body.scrollWidth, 'body.scrollWidth').to.be.at.most(root.clientWidth + 1)
  })
}

function assertComposerInViewport() {
  cy.window().then((win) => {
    const viewport = { width: win.innerWidth, height: win.innerHeight }
    cy.get('.ai-chat__composer', { timeout: 10000 }).then(($composer) => {
      const rect = $composer[0].getBoundingClientRect()
      expect(rect.top, 'composer.top').to.be.at.least(0)
      expect(rect.left, 'composer.left').to.be.at.least(0)
      expect(rect.bottom, 'composer.bottom').to.be.at.most(viewport.height + 0.5)
      expect(rect.right, 'composer.right').to.be.at.most(viewport.width + 0.5)
    })
    cy.get('.ai-chat__composer textarea').then(($input) => {
      const rect = $input[0].getBoundingClientRect()
      expect(rect.top, 'textarea.top').to.be.at.least(0)
      expect(rect.bottom, 'textarea.bottom').to.be.at.most(viewport.height + 0.5)
    })
  })
}

function assertMessageListScrollsIndependently() {
  cy.get('.ai-chat__list').then(($list) => {
    const list = $list[0]
    expect(list.scrollHeight, '消息区 scrollHeight').to.be.greaterThan(list.clientHeight + 40)
    const doc = list.ownerDocument.scrollingElement || list.ownerDocument.documentElement
    const docTopBefore = doc.scrollTop
    list.scrollTop = 0
    expect(list.scrollTop, '消息区可回到顶部').to.eq(0)
    list.scrollTop = 240
    expect(list.scrollTop, '消息区可独立滚动').to.be.greaterThan(0)
    expect(doc.scrollTop, '整页未被消息区滚动带动').to.eq(docTopBefore)
    list.scrollTop = list.scrollHeight
  })
}

function assertToolTargets() {
  cy.get('.ai-agent__timeline .ai-agent__event-row').each(($row) => {
    const height = $row[0].getBoundingClientRect().height
    expect(height, '工具行触控高度').to.be.at.least(44)
  })
}

function assertMarkerOnce(marker) {
  cy.get('.ai-chat__list').then(($list) => {
    const text = $list.text()
    const occurrences = text.split(marker).length - 1
    expect(occurrences, `${marker} 在消息区出现次数`).to.eq(1)
  })
}

/** 每种视口跑一遍完整真实流程。 */
function runAgentFlow(
  viewport: { width: number; height: number },
  label: string
) {
  cy.viewport(viewport.width, viewport.height)
  cy.request('GET', '/__control/reset?mode=stream')

  // Cypress 的 test isolation 会在新用例第一次导航时清 localStorage，
  // 因此先导航一次落 origin、再写入会话、再真正进入 /ai/chat。
  cy.visit('/ai/chat')
  cy.window().then(seedSession)
  cy.visit('/ai/chat', { onBeforeLoad: installErrorCollectors })
  cy.location('pathname').should('eq', '/ai/chat')

  // 真实渲染：AIHome 的标题 + 会话侧栏 + 历史消息。
  cy.get('.ai-chat', { timeout: 20000 }).should('be.visible')
  cy.get('.ai-chat__sidebar').should('be.visible')
  cy.contains('.ai-chat__sidebar', CONVERSATION_TITLE).should('be.visible')
  cy.contains('.ai-chat__list', '第 30 轮巡检记录', { timeout: 20000 }).should('be.visible')

  assertNoHorizontalOverflow()
  assertComposerInViewport()
  assertMessageListScrollsIndependently()

  cy.screenshot(`agent-${label}-01-initial`, { capture: 'viewport' })

  // 提交一轮真实 run。
  cy.get('.ai-chat__composer textarea').should('not.be.disabled')
  cy.get('.ai-chat__composer textarea').clear()
  cy.get('.ai-chat__composer textarea').type(TRIGGER_TEXT)
  cy.get('.ai-chat__composer button.el-button--primary').should('not.be.disabled')
  cy.get('.ai-chat__composer button.el-button--primary').click()

  // live 分区正文（SSE assistant 帧，累计文本）。
  cy.contains('.ai-chat__list', 'STREAM-PART-1', { timeout: 15000 }).should('be.visible')

  // 工具 pending：名称 + 参数摘要 + 状态徽标。
  cy.get('.ai-agent__timeline .ai-agent__event-row', { timeout: 15000 })
    .should('have.length.at.least', 1)
    .and('contain', '列出目录')
    .and('contain', TOOL_ARGS)
    .find('.ai-agent__badge')
    .should('contain', '正在执行工具')
  // 独立 live-status 行已删除：pending 态只能靠运行区块与工具行表达。
  cy.get('.ai-chat__live-status').should('not.exist')
  cy.screenshot(`agent-${label}-02-tool-pending`, { capture: 'viewport' })

  // 同一行从 pending 原地更新为 success。
  cy.get('.ai-agent__timeline .ai-agent__event-row .ai-agent__badge', { timeout: 20000 }).should(
    'contain',
    '已执行'
  )

  // 终态：落库 Markdown 正文、live 临时块退场。
  cy.contains('.ai-chat__list', FINAL_MARKER, { timeout: 25000 }).should('be.visible')
  cy.get('.ai-chat__list').should('not.contain', '巡检进行中')
  cy.get('.ai-chat__live-status').should('not.exist')
  assertMarkerOnce(FINAL_MARKER)
  cy.get('.ai-chat__list').then(($list) => {
    const text = $list.text()
    expect(text.split(TRIGGER_TEXT).length - 1, '用户消息只出现一次').to.eq(1)
  })
  cy.contains('.ai-chat__markdown', '发布前检查结果').should('be.visible')
  cy.get('.ai-chat__markdown table').should('contain', 'inbox')

  // 同一 request_id 的 user / assistant 两条消息：回看入口只挂在 assistant。
  cy.get('.ai-chat__row').then(($rows) => {
    const entries = [...$rows].filter((row) => row.querySelector('.ai-agent__meta button'))
    expect(entries.length, '回看入口数量').to.eq(1)
    expect(entries[0].classList.contains('assistant'), '回看入口只在 assistant 行').to.eq(true)
  })
  cy.contains('.ai-chat__row.user', TRIGGER_TEXT).find('.ai-agent__meta').should('not.exist')
  cy.get('.ai-chat__row .ai-agent__meta').should('have.length', 1)

  assertComposerInViewport()
  assertNoHorizontalOverflow()
  assertMessageListScrollsIndependently()
  assertToolTargets()

  cy.screenshot(`agent-${label}-03-final`, { capture: 'viewport' })

  // 回看可用：展开后能看到这次 run 的工具留痕，入口仍只有一条。
  cy.get('.ai-chat__row.assistant .ai-agent__meta button').should('contain', '回看这次运行')
  cy.get('.ai-chat__row.assistant .ai-agent__meta button').click()
  cy.get('.ai-chat__row.assistant .ai-agent__replay-list')
    .should('be.visible')
    .and('contain', '列出目录')
  cy.get('.ai-chat__row .ai-agent__meta').should('have.length', 1)
  cy.get('.ai-chat__row.assistant .ai-agent__meta button').click()
  cy.get('.ai-chat__row.assistant .ai-agent__replay-list').should('not.exist')

  // 展开工具详情（触控目标 ≥44px）。
  cy.get('.ai-agent__timeline .ai-agent__event-row').first().click()
  cy.get('.ai-agent__timeline .ai-agent__event-result')
    .should('be.visible')
    .and('contain', TOOL_RESULT)
  cy.screenshot(`agent-${label}-04-tool-expanded`, { capture: 'viewport' })

  assertNoPageErrors()
}

describe('Agent 发行版页面（真实 /ai/chat + 生产构建）', () => {
  it('2000x1200 横屏：SSE 分片、工具时间线、composer 与滚动', () => {
    runAgentFlow({ width: 2000, height: 1200 }, 'landscape-2000x1200')
  })

  it('1200x2000 竖屏：SSE 分片、工具时间线、composer 与滚动', () => {
    runAgentFlow({ width: 1200, height: 2000 }, 'portrait-1200x2000')
  })

  it('SSE fallback：历史与输入仍可用（轮询兜底）', () => {
    cy.viewport(1200, 2000)
    cy.request('GET', '/__control/reset?mode=fallback')

    cy.visit('/ai/chat')
    cy.window().then(seedSession)
    cy.visit('/ai/chat', { onBeforeLoad: installErrorCollectors })
    cy.location('pathname').should('eq', '/ai/chat')

    // 接管在途 run：SSE 503 → fallback → 轮询拿到终态。
    cy.contains('.ai-chat__list', FINAL_MARKER, { timeout: 25000 }).should('be.visible')
    cy.get('.ai-chat__composer textarea').should('not.be.disabled')
    cy.get('.ai-chat__composer button.el-button--primary').should('be.disabled')
    cy.get('.ai-chat__composer textarea').type('fallback 下仍可继续输入')
    cy.get('.ai-chat__composer button.el-button--primary').should('not.be.disabled')

    assertMarkerOnce(FINAL_MARKER)
    cy.get('.ai-chat__live-status').should('not.exist')
    cy.get('.ai-chat__row .ai-agent__meta').should('have.length', 1)
    cy.get('.ai-chat__row.user .ai-agent__meta').should('not.exist')
    cy.get('.ai-agent__timeline .ai-agent__event-row .ai-agent__badge').should('contain', '已执行')
    assertComposerInViewport()
    assertNoHorizontalOverflow()
    assertMessageListScrollsIndependently()
    cy.screenshot('agent-fallback-1200x2000', { capture: 'viewport' })

    // 确认 fallback 走的是 SSE 503 + 轮询，而不是把流「假装成功」。
    cy.request('GET', '/__control/log').then((response) => {
      const log = response.body.log || []
      expect(
        log.some((entry) => entry.sse === 503),
        'SSE 端点实际返回 503'
      ).to.eq(true)
      expect(
        log.some((entry) => entry.method === 'GET' && entry.pathname === '/api/ai/runs/run-agent-e2e'),
        '轮询 /api/ai/runs/:id 发生'
      ).to.eq(true)
    })

    assertNoPageErrors()
  })
})

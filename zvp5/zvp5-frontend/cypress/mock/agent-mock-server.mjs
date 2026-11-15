#!/usr/bin/env node
/**
 * Standalone Node mock server for the Agent (/ai/chat) Cypress lane.
 *
 * Two jobs in one process:
 *   1. serve a production build (dist) with SPA fallback, so the spec drives the
 *      real router/view/component chain instead of a hand-written fixture page;
 *   2. answer every `/api/**` call the page makes, including a chunked SSE run
 *      stream (`GET /api/ai/runs/:id/events`) with controllable delays.
 *
 * No dependencies: this file only uses node:http / node:fs. It never talks to a
 * real backend and never reads credentials.
 *
 * Usage:
 *   node cypress/mock/agent-mock-server.mjs --root /tmp/opencode/zvp5-agent-e2e/dist --port 4199
 *
 * Control plane (same origin, used by the spec through cy.request):
 *   GET /__control/reset?mode=stream|fallback
 *   GET /__control/mode?value=stream|fallback
 *   GET /__control/log
 */
import http from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import path from 'node:path'

function readArg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  if (index >= 0 && process.argv[index + 1]) {
    return process.argv[index + 1]
  }
  return fallback
}

const ROOT = path.resolve(readArg('root', '/tmp/opencode/zvp5-agent-e2e/dist'))
const PORT = Number(readArg('port', process.env.PORT || '4199'))
const HOST = readArg('host', '127.0.0.1')

const CONVERSATION_OID = 'conv-agent-e2e'
const RUN_OID = 'run-agent-e2e'
const TRIGGER_TEXT = '帮我巡检 /archive 目录，然后给出发布前检查结论。'
const TOOL_ARGS = '{"path":"/archive"}'
const TOOL_RESULT = '列出 2 个目录：inbox, outbox'
const FINAL_MARKER = 'AGENT-E2E-DONE-7F3A'
const STREAM_PARTIAL = 'STREAM-PART-1 正在扫描 /archive'

const FINAL_MARKDOWN = [
  '## 发布前检查结果',
  '',
  '已完成对 `/archive` 的只读巡检，共 **2** 个目录，未发现阻断项。',
  '',
  '| 目录 | 状态 |',
  '| --- | --- |',
  '| inbox | 正常 |',
  '| outbox | 正常 |',
  '',
  '```json',
  '{"checked":2,"directories":["inbox","outbox"]}',
  '```',
  '',
  `结论：**${FINAL_MARKER}** 校验通过。`
].join('\n')

const CONVERSATION = {
  oid: CONVERSATION_OID,
  mode: 'agent',
  title: '发布前验收 · ZVAgent',
  model: 'mock-agent',
  thinking: null,
  status: 'active',
  tokens: { prompt: 1200, completion: 480, total: 1680 },
  context: { tokens: 1420, limit: 128000, ratio: 0.0111 },
  compaction: { upto_seq: 0, summarized: false, degraded_at: null },
  message_count: 0,
  created_at: '2026-11-15T09:00:00.000Z',
  updated_at: '2026-11-16T08:00:00.000Z'
}

/** 30 条历史消息：足够撑满两种视口的消息区，验证「消息区独立滚动」。 */
function buildHistory() {
  const messages = []
  for (let index = 0; index < 30; index += 1) {
    const seq = index + 1
    const user = index % 2 === 0
    const part = String(index + 1).padStart(2, '0')
    messages.push({
      oid: `msg-history-${seq}`,
      seq,
      role: user ? 'user' : 'assistant',
      content: user
        ? `第 ${part} 轮：请把巡检范围限定在只读工具，并输出可复核的结论。`
        : [
            `### 第 ${part} 轮巡检记录`,
            '',
            `已检查归档目录下的第 ${part} 组条目，工具只读、没有产生写操作。`,
            '',
            '- 参数范围：`/archive`',
            '- 结果：未发现阻断项',
            '',
            '下一步继续核对清单中的剩余目录。'
          ].join('\n'),
      tool_calls: null,
      reasoning: null,
      tool_call_id: null,
      tool_name: null,
      round_no: null,
      request_id: null,
      usage: null,
      created_at: new Date(Date.UTC(2026, 10, 15, 9, index)).toISOString()
    })
  }
  return messages
}

function makeRun(status, rounds = 0) {
  return {
    oid: RUN_OID,
    conversation_oid: CONVERSATION_OID,
    status,
    model: 'mock-agent',
    stop_reason: status === 'succeeded' ? 'completed' : null,
    rounds,
    error_code: null,
    error_message: null,
    result_message_oid: status === 'succeeded' ? 'msg-final-assistant' : null,
    prompt_tokens: status === 'succeeded' ? 900 : 0,
    completion_tokens: status === 'succeeded' ? 180 : 0,
    total_tokens: status === 'succeeded' ? 1080 : 0,
    cancel_requested: false,
    created_at: '2026-11-16T08:10:00.000Z',
    started_at: '2026-11-16T08:10:01.000Z',
    finished_at: status === 'succeeded' ? '2026-11-16T08:10:12.000Z' : null
  }
}

const PERSISTED_TOOL_EVENT = {
  round_no: 1,
  tool_name: 'fs_list',
  tool_kind: 'read',
  args_summary: TOOL_ARGS,
  result_summary: TOOL_RESULT,
  status: 'executed'
}

function finalAssistantMessage(seq) {
  return {
    oid: 'msg-final-assistant',
    seq,
    role: 'assistant',
    content: FINAL_MARKDOWN,
    tool_calls: null,
    reasoning: null,
    tool_call_id: null,
    tool_name: null,
    round_no: 1,
    request_id: RUN_OID,
    usage: { prompt_tokens: 900, completion_tokens: 180, total_tokens: 1080 },
    created_at: '2026-11-16T08:10:12.000Z'
  }
}

let state

function reset(mode = 'stream') {
  const messages = buildHistory()
  state = {
    mode,
    messages,
    submitted: false,
    finalAdded: false,
    run: null,
    log: []
  }
  if (mode === 'fallback') {
    // 模拟「进页面时已有一个在跑的 run，而 SSE 端点不可用」：历史已在库里。
    state.messages.push({
      oid: 'msg-fallback-user',
      seq: 31,
      role: 'user',
      content: TRIGGER_TEXT,
      tool_calls: null,
      reasoning: null,
      tool_call_id: null,
      tool_name: null,
      round_no: null,
      request_id: RUN_OID,
      usage: null,
      created_at: '2026-11-16T08:10:02.000Z'
    })
    state.messages.push(finalAssistantMessage(32))
    state.finalAdded = true
    state.run = makeRun('running', 1)
  }
}

reset('stream')

function log(entry) {
  state.log.push(entry)
  if (state.log.length > 500) {
    state.log.shift()
  }
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8'
}

function sendJson(res, status, body, headers = {}) {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
    ...headers
  })
  res.end(payload)
}

function ok(res, data, metadata) {
  sendJson(res, 200, {
    status: 'ok',
    code: 200,
    data,
    ...(metadata ? { metadata } : {})
  })
}

function queryOf(url) {
  return url.searchParams
}

function numberParam(url, name, fallback) {
  const raw = url.searchParams.get(name)
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

function messagesPage(url) {
  const afterSeq = numberParam(url, 'after_seq', 0)
  const limit = Math.min(100, Math.max(1, numberParam(url, 'limit', 50)))
  const sorted = [...state.messages].sort((a, b) => a.seq - b.seq)
  const fresh = sorted.filter((item) => item.seq > afterSeq)
  return {
    messages: fresh.slice(0, limit),
    has_more: fresh.length > limit,
    total: sorted.length
  }
}

function runDetail(url) {
  // fallback 场景：SSE 不可用，轮询第一次就拿到终态，模拟「任务在别处已经跑完」。
  if (state.mode === 'fallback' && state.run && state.run.status === 'running') {
    state.run = makeRun('succeeded', 1)
  }
  const afterRound = numberParam(url, 'after_round', 0)
  const sinceSeq = url.searchParams.has('since_msg_seq')
    ? numberParam(url, 'since_msg_seq', 0)
    : null
  const events = afterRound < 1 && state.run?.status === 'succeeded' ? [PERSISTED_TOOL_EVENT] : []
  const messages =
    sinceSeq === null
      ? []
      : state.messages.filter((item) => item.seq > sinceSeq).sort((a, b) => a.seq - b.seq)
  return {
    run: state.run ?? makeRun('interrupted'),
    events,
    proposals: [],
    messages
  }
}

function userFixture() {
  return {
    _id: 'user-agent-e2e',
    id: 1001,
    name: 'E2E 验收管理员',
    roles: ['admin'],
    group: [],
    class_id: '',
    banned: false,
    shouldResetPassword: false
  }
}

function handleApi(req, res, url, pathname) {
  const method = req.method || 'GET'
  log({ method, pathname, query: url.search })

  if (method === 'GET' && pathname === '/api/ai/v1/models') {
    sendJson(res, 200, {
      object: 'list',
      data: [
        {
          id: 'mock-agent',
          object: 'model',
          owned_by: 'e2e-mock',
          context: 128000
        }
      ]
    })
    return
  }

  if (method === 'GET' && pathname === '/api/ai/conversations') {
    ok(
      res,
      {
        conversations: [{ ...CONVERSATION, message_count: state.messages.length }],
        next_cursor: null
      },
      { size: 1 }
    )
    return
  }

  if (method === 'POST' && pathname === '/api/ai/conversations') {
    ok(res, { ...CONVERSATION, message_count: state.messages.length })
    return
  }

  if (pathname === `/api/ai/conversations/${CONVERSATION_OID}`) {
    ok(res, { ...CONVERSATION, message_count: state.messages.length })
    return
  }

  if (pathname === `/api/ai/conversations/${CONVERSATION_OID}/messages`) {
    ok(res, messagesPage(url))
    return
  }

  if (method === 'POST' && pathname === `/api/ai/conversations/${CONVERSATION_OID}/runs`) {
    readBody(req)
      .then((body) => {
        const content = typeof body?.content === 'string' && body.content ? body.content : TRIGGER_TEXT
        if (!state.submitted) {
          state.submitted = true
          state.run = makeRun('queued', 0)
          state.messages.push({
            oid: 'msg-live-user',
            seq: state.messages.length + 1,
            role: 'user',
            content,
            tool_calls: null,
            reasoning: null,
            tool_call_id: null,
            tool_name: null,
            round_no: null,
            request_id: RUN_OID,
            usage: null,
            created_at: '2026-11-16T08:10:02.000Z'
          })
        }
        sendJson(res, 202, {
          status: 'ok',
          code: 202,
          data: {
            run_id: RUN_OID,
            message_oid: 'msg-live-user',
            status: 'queued',
            deduped: false
          }
        })
      })
      .catch(() => sendJson(res, 400, { detail: 'AI_INVALID_REQUEST' }))
    return
  }

  if (method === 'GET' && pathname === '/api/ai/runs') {
    const active = url.searchParams.get('active')
    if (active === '1') {
      ok(res, { runs: state.run && ['queued', 'running'].includes(state.run.status) ? [state.run] : [] })
      return
    }
    ok(res, { runs: state.run ? [state.run] : [] })
    return
  }

  if (method === 'GET' && pathname === `/api/ai/runs/${RUN_OID}`) {
    ok(res, runDetail(url))
    return
  }

  if (method === 'POST' && pathname === `/api/ai/runs/${RUN_OID}/cancel`) {
    if (state.run) {
      state.run = { ...state.run, status: 'cancelled', stop_reason: 'cancelled' }
    }
    ok(res, { status: 'cancelled' })
    return
  }

  if (method === 'GET' && pathname === `/api/ai/runs/${RUN_OID}/events`) {
    if (state.mode === 'fallback') {
      log({ method, pathname, sse: 503 })
      sendJson(res, 503, { detail: 'AI_RUN_EVENTS_UNSUPPORTED' })
      return
    }
    log({ method, pathname, sse: 200 })
    void streamRunEvents(req, res)
    return
  }

  if (method === 'GET' && pathname === '/api/ai/agent/proposals') {
    sendJson(res, 200, { proposals: [] })
    return
  }

  if (method === 'GET' && pathname === '/api/system/maintenance') {
    ok(res, { enabled: false, since: null, announcement: null, admin: true })
    return
  }

  if (method === 'GET' && pathname.startsWith('/api/v1/users/')) {
    ok(res, userFixture())
    return
  }

  if (method === 'GET' && pathname === '/api/v1/notifications') {
    ok(res, { version: 1, notifications: [] })
    return
  }

  if (method === 'GET' && pathname === '/api/v1/notifications/read') {
    ok(res, { read: {} })
    return
  }

  // 兜底：页面外壳会拉一些与本任务无关的数据，给空壳避免 404 噪声。
  if (pathname.startsWith('/api/')) {
    ok(res, null, { size: 0 })
    return
  }

  sendJson(res, 404, { status: 'error', detail: 'NOT_FOUND' })
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw) {
        resolve(null)
        return
      }
      try {
        resolve(JSON.parse(raw))
      } catch {
        resolve(null)
      }
    })
  })
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function streamRunEvents(req, res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no'
  })
  res.flushHeaders?.()

  let closed = false
  const onClose = () => {
    closed = true
  }
  req.on('close', onClose)
  res.on('close', onClose)

  const send = (frame) => {
    if (closed || res.writableEnded) {
      return false
    }
    res.write(`data: ${JSON.stringify(frame)}\n\n`)
    return true
  }

  const snapshot = (seq, run, data = {}) => ({
    type: 'snapshot',
    seq,
    run_id: RUN_OID,
    data: {
      run,
      events: data.events ?? [],
      proposals: data.proposals ?? [],
      messages: data.messages ?? []
    },
    live: data.live ?? { assistant: [], tools: [] }
  })

  if (state.run) {
    state.run = { ...state.run, status: 'running' }
  } else {
    state.run = makeRun('running', 0)
  }

  send(snapshot(1, state.run))
  await sleep(500)
  if (closed) return

  send({
    type: 'assistant',
    seq: 2,
    run_id: RUN_OID,
    round_no: 1,
    stream_key: `${RUN_OID}:1`,
    content: `## 巡检进行中\n\n${STREAM_PARTIAL}`
  })
  await sleep(900)
  if (closed) return

  send({
    type: 'tool',
    seq: 3,
    run_id: RUN_OID,
    round_no: 1,
    tool_call_id: 'call-fs-list-1',
    tool_name: 'fs_list',
    tool_kind: 'read',
    status: 'pending',
    args_summary: TOOL_ARGS
  })
  await sleep(2200)
  if (closed) return

  send({
    type: 'assistant',
    seq: 4,
    run_id: RUN_OID,
    round_no: 1,
    stream_key: `${RUN_OID}:1`,
    content: `## 巡检进行中\n\n${STREAM_PARTIAL}，已读取目录清单。`
  })
  await sleep(1200)
  if (closed) return

  send({
    type: 'tool',
    seq: 5,
    run_id: RUN_OID,
    round_no: 1,
    tool_call_id: 'call-fs-list-1',
    tool_name: 'fs_list',
    tool_kind: 'read',
    status: 'success',
    args_summary: TOOL_ARGS,
    result_summary: TOOL_RESULT
  })
  await sleep(700)
  if (closed) return

  if (!state.finalAdded) {
    state.finalAdded = true
    state.messages.push(finalAssistantMessage(state.messages.length + 1))
  }
  state.run = makeRun('succeeded', 1)
  // 终态 snapshot：live 清空（正文改由落库消息承担），events 落库留痕。
  send(
    snapshot(6, state.run, {
      events: [PERSISTED_TOOL_EVENT],
      messages: [state.messages[state.messages.length - 1]],
      live: { assistant: [], tools: [] }
    })
  )
  await sleep(150)
  if (closed) return

  send({
    type: 'terminal',
    seq: 7,
    run_id: RUN_OID,
    status: 'succeeded',
    stop_reason: 'completed',
    error_code: null
  })
  await sleep(80)
  if (!closed && !res.writableEnded) {
    res.write('data: [DONE]\n\n')
    res.end()
  }
}

function serveStatic(res, pathname) {
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
  const candidate = path.resolve(ROOT, relative)
  const insideRoot = candidate === ROOT || candidate.startsWith(`${ROOT}${path.sep}`)
  let file = insideRoot && existsSync(candidate) && statSync(candidate).isFile() ? candidate : null
  if (!file) {
    // SPA fallback（history 路由）。
    file = path.join(ROOT, 'index.html')
  }
  if (!existsSync(file)) {
    res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end(`dist not found: ${ROOT}`)
    return
  }
  const ext = path.extname(file).toLowerCase()
  const headers = {
    'Content-Type': MIME[ext] ?? 'application/octet-stream',
    'Cache-Control': 'no-store'
  }
  const size = statSync(file).size
  headers['Content-Length'] = size
  res.writeHead(200, headers)
  createReadStream(file).pipe(res)
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://${HOST}:${PORT}`)
  const pathname = decodeURIComponent(url.pathname)

  if (pathname === '/__control/reset') {
    reset(url.searchParams.get('mode') === 'fallback' ? 'fallback' : 'stream')
    sendJson(res, 200, { ok: true, mode: state.mode })
    return
  }

  if (pathname === '/__control/mode') {
    const value = url.searchParams.get('value') === 'fallback' ? 'fallback' : 'stream'
    state.mode = value
    sendJson(res, 200, { ok: true, mode: state.mode })
    return
  }

  if (pathname === '/__control/log') {
    sendJson(res, 200, { mode: state.mode, log: state.log })
    return
  }

  if (pathname === '/__control/health') {
    sendJson(res, 200, { ok: true, root: ROOT })
    return
  }

  if (pathname.startsWith('/api/')) {
    handleApi(req, res, url, pathname)
    return
  }

  serveStatic(res, pathname)
})

server.listen(PORT, HOST, () => {
  console.log(`[agent-mock] http://${HOST}:${PORT} root=${ROOT}`)
})

function shutdown() {
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 500).unref()
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

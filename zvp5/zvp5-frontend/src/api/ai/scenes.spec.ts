import { beforeEach, expect, it, vi } from 'vitest'
import { ocrFile, submitSceneRun } from './scenes'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

function ok(data: unknown, status = 200) {
  return { status, data: { status: 'ok', code: status, data }, headers: {} }
}

beforeEach(() => {
  axiosMock.mockReset()
})

it('提交场景 run：POST /ai/scenes/{scene}/runs，带上指令与参考内容', async () => {
  axiosMock.mockResolvedValueOnce(
    ok({ run_id: 'r1', conversation_oid: 'c1' }, 202)
  )

  const result = await submitSceneRun('file_edit', '看看这段配置', 'listen 8080')

  expect(result).toEqual({ run_id: 'r1', conversation_oid: 'c1' })
  expect(axiosMock).toHaveBeenCalledWith(
    expect.objectContaining({
      method: 'post',
      url: '/ai/scenes/file_edit/runs',
      data: { content: '看看这段配置', context: 'listen 8080' }
    })
  )
})

it('提交场景 run：没有参考内容时不带 context 字段', async () => {
  axiosMock.mockResolvedValueOnce(ok({ run_id: 'r2', conversation_oid: 'c2' }, 202))

  await submitSceneRun('terminal', '磁盘满了怎么排查')

  expect(axiosMock).toHaveBeenCalledWith(
    expect.objectContaining({
      url: '/ai/scenes/terminal/runs',
      data: { content: '磁盘满了怎么排查' }
    })
  )
})

it('文件 OCR：POST /ai/ocr/file，返回识别文本', async () => {
  axiosMock.mockResolvedValueOnce(ok({ text: '第一行' }))

  const result = await ocrFile('/www/zvms4/a.png')

  expect(result).toEqual({ text: '第一行' })
  expect(axiosMock).toHaveBeenCalledWith(
    expect.objectContaining({
      method: 'post',
      url: '/ai/ocr/file',
      data: { path: '/www/zvms4/a.png' }
    })
  )
})

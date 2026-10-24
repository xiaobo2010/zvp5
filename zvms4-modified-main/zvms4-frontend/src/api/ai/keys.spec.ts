import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createKey, deleteKey, listKeys, updateKey } from './keys'
import { AiRequestError } from './errors'

const { axiosMock } = vi.hoisted(() => ({ axiosMock: vi.fn() }))

vi.mock('@/plugins/axios', () => ({
  default: axiosMock,
  baseURL: '/api'
}))

const keyFixture = {
  oid: '69b7a4ad0243d973f1e1a2c1',
  scope: 'shared' as const,
  owner_oid: '69b7a4ad0243d973f1e1a2b2',
  name: 'demo',
  kind: 'openai_compat',
  base_url: 'https://api.example.com',
  models: ['gpt-4o-mini'],
  headers: { 'X-Api-Key': '****' },
  api_path: null,
  key_hint: '7890',
  enabled: true,
  created_at: '2026-10-24T00:00:00',
  updated_at: '2026-10-24T00:00:00',
  owned: false
}

beforeEach(() => {
  axiosMock.mockReset()
})

describe('keys API 契约', () => {
  it('拆开 envelope 只返回 data', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 200,
      data: { status: 'ok', code: 200, data: [keyFixture] }
    })

    const result = await listKeys()

    expect(result).toEqual([keyFixture])
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'get', url: '/ai/keys' })
    )
  })

  it('创建时把请求字段按后端形状发出去', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 201,
      data: { status: 'ok', code: 201, data: keyFixture }
    })

    await createKey({
      scope: 'personal',
      name: 'demo',
      base_url: 'https://api.example.com',
      api_key: 'sk-1234567890'
    })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/ai/keys',
        data: {
          scope: 'personal',
          name: 'demo',
          base_url: 'https://api.example.com',
          api_key: 'sk-1234567890'
        }
      })
    )
  })

  it('更新走 PATCH 且只带改动的字段', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 200,
      data: { status: 'ok', code: 200, data: { ...keyFixture, enabled: false } }
    })

    await updateKey(keyFixture.oid, { enabled: false })

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'patch',
        url: `/ai/keys/${keyFixture.oid}`,
        data: { enabled: false }
      })
    )
  })

  it('删除返回 204 且不解析响应体', async () => {
    axiosMock.mockResolvedValueOnce({ status: 204, data: '' })

    await expect(deleteKey(keyFixture.oid)).resolves.toBeUndefined()
    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: 'delete', url: `/ai/keys/${keyFixture.oid}` })
    )
  })

  it('错误响应抛出带错误码的 AI 错误', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 409,
      data: { detail: 'AI_KEY_DUPLICATE' },
      headers: {}
    })

    await expect(
      createKey({
        scope: 'shared',
        name: 'demo',
        base_url: 'https://api.example.com',
        api_key: 'sk-1234567890'
      })
    ).rejects.toMatchObject({
      name: 'AiRequestError',
      status: 409,
      code: 'AI_KEY_DUPLICATE'
    })
  })

  it('错误对象是 AiRequestError 实例', async () => {
    axiosMock.mockResolvedValueOnce({
      status: 400,
      data: { detail: 'AI_KEY_INVALID' },
      headers: {}
    })

    const error = await createKey({
      scope: 'shared',
      name: 'demo',
      base_url: 'https://api.example.com',
      api_key: 'sk-1234567890'
    }).catch((err: unknown) => err)

    expect(error).toBeInstanceOf(AiRequestError)
  })
})

import { describe, expect, it } from 'vitest'
import type { MyDeviceStatus } from '@/api/user/device'
import {
  isPrivilegedRoles,
  isValidXuehaiId,
  normalizeXuehaiId,
  primaryDeviceBanReason,
  primaryDeviceCardState,
  primaryDeviceErrorMessage,
  primaryDeviceGateMode
} from './primaryDeviceLogic'

/**
 * 主设备自助面的纯逻辑：门形态、动作矩阵、错误文案。
 *
 * 只测「判得对不对」，不测组件渲染。
 */

function makeStatus(overrides: Partial<MyDeviceStatus> = {}): MyDeviceStatus {
  return {
    xuehai_id: '1001',
    primary_xuehai_id: null,
    device_banned: false,
    device_ban_reason: null,
    device_ban_until: null,
    ...overrides
  }
}

describe('primaryDeviceGateMode', () => {
  it('未登录不弹门', () => {
    expect(
      primaryDeviceGateMode({ isLogin: false, roles: ['student'], status: makeStatus() })
    ).toBe('hidden')
  })

  it.each(['admin', 'department', 'secretary', 'teacher'])(
    '特权角色 %s 在学海设备上未绑定也要绑',
    (role) => {
      expect(isPrivilegedRoles([role])).toBe(true)
      expect(primaryDeviceGateMode({ isLogin: true, roles: [role], status: makeStatus() })).toBe(
        'bindOnDevice'
      )
    }
  )

  it.each(['admin', 'department', 'secretary', 'teacher'])(
    '特权角色 %s 在 PC 上未绑定不校验',
    (role) => {
      expect(
        primaryDeviceGateMode({
          isLogin: true,
          roles: [role],
          status: makeStatus({ xuehai_id: '' })
        })
      ).toBe('hidden')
    }
  )

  it('未绑定 + 本机是学海设备 → 在本机绑定', () => {
    expect(
      primaryDeviceGateMode({ isLogin: true, roles: ['student'], status: makeStatus() })
    ).toBe('bindOnDevice')
  })

  it('未绑定 + 本机不是学海设备 → 只能退出登录', () => {
    expect(
      primaryDeviceGateMode({
        isLogin: true,
        roles: ['student'],
        status: makeStatus({ xuehai_id: '' })
      })
    ).toBe('logoutOnly')
  })

  it('已绑定不弹门', () => {
    expect(
      primaryDeviceGateMode({
        isLogin: true,
        roles: ['student'],
        status: makeStatus({ primary_xuehai_id: '1001' })
      })
    ).toBe('hidden')
  })

  it('状态读取失败（null）按 fail-open 不弹门', () => {
    expect(primaryDeviceGateMode({ isLogin: true, roles: ['student'], status: null })).toBe(
      'hidden'
    )
  })
})

describe('primaryDeviceCardState', () => {
  it('未绑定 + 本机是学海设备：只允许绑定', () => {
    expect(primaryDeviceCardState(makeStatus())).toEqual({
      bound: false,
      isXuehaiDevice: true,
      isPrimaryDevice: false,
      canBind: true,
      canChange: false,
      canUnbind: false,
      readOnlyReason: 'none'
    })
  })

  it('未绑定 + 本机不是学海设备：没有任何动作', () => {
    const state = primaryDeviceCardState(makeStatus({ xuehai_id: '' }))
    expect(state.canBind).toBe(false)
    expect(state.canChange).toBe(false)
    expect(state.canUnbind).toBe(false)
    expect(state.readOnlyReason).toBe('none')
  })

  it('已绑定 + 本机是主设备：可换绑、可解绑', () => {
    const state = primaryDeviceCardState(makeStatus({ primary_xuehai_id: '1001' }))
    expect(state.isPrimaryDevice).toBe(true)
    expect(state.canBind).toBe(false)
    expect(state.canChange).toBe(true)
    expect(state.canUnbind).toBe(true)
    expect(state.readOnlyReason).toBe('none')
  })

  it('已绑定 + 本机是另一台学海设备：只读', () => {
    const state = primaryDeviceCardState(
      makeStatus({ xuehai_id: '1002', primary_xuehai_id: '1001' })
    )
    expect(state.canChange).toBe(false)
    expect(state.canUnbind).toBe(false)
    expect(state.readOnlyReason).toBe('notPrimary')
  })

  it('已绑定 + 本机不是学海设备：只读，提示换到主设备', () => {
    const state = primaryDeviceCardState(
      makeStatus({ xuehai_id: '', primary_xuehai_id: '1001' })
    )
    expect(state.canChange).toBe(false)
    expect(state.canUnbind).toBe(false)
    expect(state.readOnlyReason).toBe('notXuehai')
  })
})

describe('isValidXuehaiId', () => {
  it('接受 1–32 位纯数字并去掉首尾空白', () => {
    expect(isValidXuehaiId('1')).toBe(true)
    expect(isValidXuehaiId(' 123456 ')).toBe(true)
    expect(isValidXuehaiId('0'.repeat(32))).toBe(true)
    expect(normalizeXuehaiId(' 123456 ')).toBe('123456')
  })

  it('拒绝空串、字母、空格、全角数字与超长值', () => {
    expect(isValidXuehaiId('')).toBe(false)
    expect(isValidXuehaiId('12a3')).toBe(false)
    expect(isValidXuehaiId('12 3')).toBe(false)
    expect(isValidXuehaiId('１２３')).toBe(false)
    expect(isValidXuehaiId('0'.repeat(33))).toBe(false)
  })
})

describe('primaryDeviceErrorMessage', () => {
  it('优先透出后端给的中文 message', () => {
    expect(
      primaryDeviceErrorMessage(
        { status: 403, code: 'AI_HTTP_403', message: '只能在主学海设备上更换主学海设备' },
        '兜底'
      )
    ).toBe('只能在主学海设备上更换主学海设备')
  })

  it('字符串 detail 被塞进 code 时也能透出', () => {
    expect(
      primaryDeviceErrorMessage(
        { status: 400, code: '首次绑定只能在目标设备上发起', message: '' },
        '兜底'
      )
    ).toBe('首次绑定只能在目标设备上发起')
  })

  it('内部代号回落兜底文案', () => {
    expect(
      primaryDeviceErrorMessage({ status: 500, code: 'AI_HTTP_500', message: 'AI_HTTP_500' }, '兜底')
    ).toBe('兜底')
    expect(primaryDeviceErrorMessage(new Error('AI_UNAUTHORIZED'), '兜底')).toBe('兜底')
  })

  it('普通异常与空值都回落兜底文案', () => {
    expect(primaryDeviceErrorMessage(new Error('网络断了'), '兜底')).toBe('网络断了')
    expect(primaryDeviceErrorMessage(null, '兜底')).toBe('兜底')
    expect(primaryDeviceErrorMessage('boom', '兜底')).toBe('兜底')
  })
})

describe('primaryDeviceBanReason', () => {
  it('有原因用原因并去掉首尾空白', () => {
    expect(
      primaryDeviceBanReason(
        makeStatus({ device_banned: true, device_ban_reason: '  违规使用  ' }),
        '兜底'
      )
    ).toBe('违规使用')
  })

  it('原因缺失用兜底文案', () => {
    expect(
      primaryDeviceBanReason(makeStatus({ device_banned: true, device_ban_reason: null }), '兜底')
    ).toBe('兜底')
  })
})

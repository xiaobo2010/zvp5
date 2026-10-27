import type { MyDeviceStatus } from '@/api/user/device'

/**
 * 主设备自助面的纯逻辑：强制门形态、卡片动作矩阵、错误与封禁文案。
 *
 * 不碰 Vue 与网络，组件只负责把结果画出来，因此可以单测。
 */

/** 不受主设备闸限制的角色（与 services/device_guard.py 的 PRIVILEGED_ROLES 一致）。 */
export const PRIVILEGED_ROLES = ['admin', 'department', 'secretary'] as const

export function isPrivilegedRoles(roles: readonly string[]): boolean {
  return PRIVILEGED_ROLES.some((role) => roles.includes(role))
}

/** 强制门的三种形态：不弹、本机可绑定、只能退出登录。 */
export type PrimaryDeviceGateMode = 'hidden' | 'bindOnDevice' | 'logoutOnly'

export interface PrimaryDeviceGateInput {
  isLogin: boolean
  roles: readonly string[]
  /** null = 尚未读取成功；读取失败按 fail-open 处理，不锁人。 */
  status: MyDeviceStatus | null
}

/**
 * 门形态判定。
 *
 * 口径（2026-10-26 调整）：**学海设备上所有角色未绑定都必须绑**；PC（非学海设备）
 * 上特权角色不做主设备校验（允许特权账号用电脑登录），普通学生只能退出登录
 * 换平板。读取失败说明不了用户没绑定，fail-open 不锁人，与后端登录口径一致。
 */
export function primaryDeviceGateMode(input: PrimaryDeviceGateInput): PrimaryDeviceGateMode {
  if (!input.isLogin) {
    return 'hidden'
  }
  const status = input.status
  if (!status || status.primary_xuehai_id !== null) {
    return 'hidden'
  }
  const onXuehaiDevice = status.xuehai_id !== ''
  if (!onXuehaiDevice && isPrivilegedRoles(input.roles)) {
    return 'hidden'
  }
  return onXuehaiDevice ? 'bindOnDevice' : 'logoutOnly'
}

/** 卡片只读原因：none 可操作；notPrimary 本机不是主设备；notXuehai 本机不是学海设备。 */
export type PrimaryDeviceReadOnlyReason = 'none' | 'notPrimary' | 'notXuehai'

export interface PrimaryDeviceCardState {
  bound: boolean
  isXuehaiDevice: boolean
  isPrimaryDevice: boolean
  canBind: boolean
  canChange: boolean
  canUnbind: boolean
  readOnlyReason: PrimaryDeviceReadOnlyReason
}

/**
 * 「我的」页主设备卡的动作矩阵。
 *
 * 首次绑定只能在本机发起；换绑/解绑只能在当前主设备发起——这两条都写死在
 * 后端，前端只按状态决定按钮是否可用，不做乐观放行。
 */
export function primaryDeviceCardState(status: MyDeviceStatus): PrimaryDeviceCardState {
  const isXuehaiDevice = status.xuehai_id !== ''
  const bound = status.primary_xuehai_id !== null
  const isPrimaryDevice =
    bound && isXuehaiDevice && status.primary_xuehai_id === status.xuehai_id

  let readOnlyReason: PrimaryDeviceReadOnlyReason = 'none'
  if (bound && !isPrimaryDevice) {
    readOnlyReason = isXuehaiDevice ? 'notPrimary' : 'notXuehai'
  }

  return {
    bound,
    isXuehaiDevice,
    isPrimaryDevice,
    canBind: !bound && isXuehaiDevice,
    canChange: isPrimaryDevice,
    canUnbind: isPrimaryDevice,
    readOnlyReason
  }
}

export function normalizeXuehaiId(raw: string): string {
  return raw.trim()
}

/** 与后端 _validated_device_id 对齐：1–32 位 ASCII 纯数字。 */
export function isValidXuehaiId(raw: string): boolean {
  return /^[0-9]{1,32}$/.test(normalizeXuehaiId(raw))
}

/** 内部代号（AI_HTTP_400 之类）不是人话，展示时换成兜底文案。 */
function isInternalCode(value: string): boolean {
  return /^AI_[A-Z0-9_]+$/.test(value)
}

/**
 * 从异常里取出能直接展示的中文提示。
 *
 * 后端 400/403 的 detail 是整句中文，aiRequest 会把它放进 code（此时 message 为空）；
 * 结构化 detail 则把 message 填好。两者都优先透出，只有内部代号才回落兜底文案。
 */
export function primaryDeviceErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>
    const message = typeof record.message === 'string' ? record.message.trim() : ''
    if (message && !isInternalCode(message)) {
      return message
    }
    const code = typeof record.code === 'string' ? record.code.trim() : ''
    if (code && !isInternalCode(code)) {
      return code
    }
  }
  return fallback
}

/** 设备封禁原因：后端没给原因时用统一文案，不留空洞。 */
export function primaryDeviceBanReason(status: MyDeviceStatus, fallback: string): string {
  const reason = status.device_ban_reason?.trim()
  return reason || fallback
}

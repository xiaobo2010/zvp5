/**
 * 跨模块通用文案。
 *
 * api 层的 200-status-error 自弹过去是英文标题（Error when fetching data (400)），
 * 这里收口成中文，消息体仍是后端原始 detail，便于排查。
 */
export const zhCN = {
  fetchFailed: '获取数据失败（{code}）',
  logListFetchFailed: '日志列表加载失败（{code}）',
  userUpdateFailed: '更新用户失败（{code}）',
  publicKeyFetchFailed: '获取登录公钥失败',
  passwordResetFailed: '密码重置失败'
}

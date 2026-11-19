export const zhCN = {
  title: '系统维护中',
  defaultDescription: '平台正在进行维护，请稍后再试。',
  announcementLabel: '公告',
  startedAt: '维护开始于',
  recheck: '重新检查',
  checking: '检查中…',
  autoCheckNote: '维护状态每 10 分钟自动检查一次',
  adminEntry: '管理员入口',
  ended: {
    title: '维护已结束',
    enter: '进入平台'
  },
  notMaintaining: '当前未处于维护状态',
  // 维护期间登录被拒（v4.2.4-2 R4）：后端 /api/v1/users/auth 对非管理员返回 503。
  loginBlocked: '系统维护中，仅管理员可登录'
}
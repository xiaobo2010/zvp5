export const zhCN = {
  tabs: {
    maintenance: '维护模式'
  },
  maintenance: {
    title: '系统维护模式',
    statusNormal: '正常运行中',
    statusMaintaining: '维护中（自 {time}）',
    enable: '开启维护模式',
    disable: '关闭维护模式',
    enableConfirm:
      '开启后全平台将停机：所有业务接口返回 503，普通用户只能看到维护公告页。确定要开启吗？',
    disableConfirm: '确定要关闭维护模式吗？关闭后平台立即恢复对外服务。',
    reasonLabel: '维护原因',
    reasonHint: '仅内部审计，不会对外展示',
    announcementLabel: '公告文案',
    announcementHint: '显示在维护公告页，最多 300 字',
    enableSuccess: '已开启维护模式',
    disableSuccess: '已关闭维护模式',
    statusFailed: '维护状态读取失败',
    reasonRequired: '请输入维护原因',
    announcementTooLong: '公告不能超过 300 字'
  }
}
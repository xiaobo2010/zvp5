export const zhCN = {
  home: '首页',
  profile: '我的',
  activity: '义工列表',
  manage: '平台管理',
  server: '服务器管理',
  ai: 'AI 网关',
  create: '创建义工',
  register: '批量登记',
  registerHint:
    '成员可前往批量登记页逐个录入或粘贴名单：该页会自动带上本活动，提交后回到活动详情继续上传义工单。',
  about: '关于',
  logout: '退出登录',
  reset: '密码修改',
  banned: {
    title: '账号已被封禁',
    message: '由于{reason}，您的账号已被封禁。',
    reasonFallback: '违反平台使用规定',
    permanent: '永久封禁',
    until: '封禁到期：{time}',
    appeal: '违规使用平板是严重的违纪行为。',
    autoLogoutIn: '将在 {seconds} 秒后自动退出登录',
    ack: '我知道了'
  },
  language: '',
  dark: '夜间模式',
  toggleDark: '切换深浅色模式',
  languages: {
    'zh-CN': '',
    'en-US': '',
  },
  pages: {
    NotFound: {
      title: '404 页面未找到',
      description: '您访问的页面不存在。',
      action: {
        back: '返回首页',
        report: '',
      },
    },
    SomethingWentWrong: {
      title: '',
      description: '',
    },
  },
  login: {
    pageTitle: '登录义工平台',
    actions: {
      login: '登录',
      reset: '重置',
      logout: '登出'
    },
    motto: '励志 进取 勤奋 健美',
    form: {
      account: '学号',
      password: '密码',
    },
    device: {
      tempTitle: '临时密码登录',
      tempHint: '本机不是该账号的主学海设备。请输入团支书或管理员签发的 8 位临时密码。',
      tempPlaceholder: '8 位数字临时密码',
      tempNote: '验证窗口 5 分钟内有效；临时密码 30 分钟内有效，且只能用一次。',
      tempSubmit: '临时密码登录',
      tempBack: '返回',
      tempInvalid: '临时密码错误或已失效。请核对后重试；若已过期，请返回重新登录以获取新的验证挑战。',
      tempMismatch: '临时密码只能在一台设备上使用。请返回后在本机重新登录以获取新的验证挑战。',
      tempFailed: '临时密码登录失败，请稍后重试。',
      tempSession: '临时会话已建立，1 小时内有效。如需将本机设为主设备，请联系管理员清除旧的主设备绑定。',
      required: '普通用户需使用学海平板登录本应用。如确需在其它设备登入，请让团支书或管理员签发临时密码。',
      challengeMissing: '本机不是主设备，且未取到验证挑战。请返回重新登录。',
      bannedTitle: '这台设备已被限制登录',
      bannedFallback: '违规使用平板',
      bannedAck: '我知道了'
    },
  },
  breadcrumbs: {
    view: {
      home: '查看',
      specific: '义工列表',
    },
    actions: {
      create: '创建',
      edit: '编辑',
      delete: '删除',
      search: '搜索',
      filter: '过滤',
      sort: '排序',
      refresh: '刷新',
    },
    settings: {
      profile: '个人资料',
      logout: '登出',
    },
    manage: '平台管理',
  },
}

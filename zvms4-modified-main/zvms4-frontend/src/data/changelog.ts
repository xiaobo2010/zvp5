export interface ChangelogEntry {
  version: string
  date: string
  items: string[]
}

export const changelog: ChangelogEntry[] = [
  {
    version: 'v4.2.0-3',
    date: '2026-10-11',
    items: [
      '「身份」栏不再空白：学生用户现在会显示班级徽标或「学生」标签（个人信息页与用户详情卡）',
      '修复用户详情显示「班级不存在」的问题：用户与班级信息读取改为新旧数据双库兼容解析，此前无法查看的约 785 个新用户也恢复正常',
      '系统日志中未登录的请求现在显示「未登录」，不再误标为「无此用户」',
      '删除义工活动后自动返回活动列表'
    ]
  },
  {
    version: 'v4.2.0-2',
    date: '2026-10-11',
    items: [
      '平台迁移至全新部署，全部用户数据完整保留',
      '平台图标全套更新',
      '修复：导出用户列表时，无班级用户导致导出失败的问题',
      '修复：删除用户按钮不可用、班级分组类型过滤、自助修改密码返回提示等问题',
      '前端构建类型错误清零，页面更稳定'
    ]
  }
]

export const APP_VERSION = changelog[0].version

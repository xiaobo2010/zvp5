// 用户详情页(封禁弹窗)新增文案。不放 manage.ts:该文件同时被设备封禁/SSH/规则页
// 编辑,并发改动容易冲突;用户页专属的时长文案单独一个命名空间,按页归属更清晰。
export const zhCN = {
  ban: {
    // 时长选择
    durationLabel: '封禁时长',
    durationDays: '{n} 天',
    durationPermanent: '永久',
    durationCustom: '自定义',
    customPlaceholder: '1 - 36500',
    customUnit: '天',
    customInvalid: '请输入 1 - 36500 的整数天数',
    // 选择后的即时预览
    autoUnbanAt: '将于 {time} 自动解封',
    permanentHint: '永久封禁，需管理员手动解封',
    dialogNotice: '选择封禁时长后提交：到期自动解封，也可以随时手动解封。',
    // 已封禁标签/说明里的到期表达
    periodUntil: '至 {time}',
    periodPermanent: '永久'
  },
  volunteerAnalysis: {
    action: 'AI 分析',
    title: '义工分析',
    partialFailed: '部分资料未能获取，分析参考内容可能不完整'
  }
}

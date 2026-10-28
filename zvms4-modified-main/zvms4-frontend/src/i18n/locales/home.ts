export const zhCN = {
  greeting: '{greet}，{name}！',
  greetings: {
    morning: '早上好',
    afternoon: '下午好',
    evening: '晚上好'
  },
  positions: {
    student: '学生',
    secretary: '团支书',
    department: '学生会',
    admin: '管理员'
  },
  labels: {
    name: '姓名',
    number: '学号',
    class: '班级',
    identify: '身份',
    status: '账号状态',
    past: '过往身份',
    nonPast: '无'
  },
  status: {
    normal: '正常',
    banned: '已封禁'
  },
  panels: {
    information: {
      title: '个人信息'
    },
    time: {
      title: '义工时间',
      discount: '显示折算',
      discount_desc:
        '根据相关规定，校内活动时间达标的，可以按 1/3 的比例将超出部分折算为校外时长；而校外义工时间达标的，可以按 1/2 的比例将超出部分折算为校内时长。每种折算在每个模式下不得超过6小时。',
      unit: '小时',
      least: '{type}: 至少 {least} 小时',
      lack: '还差',
      exceed: '超出'
    },
    review: {
      title: '待审核活动',
      description: '需要审核的义工活动',
      empty: '暂无待审核活动',
      emptyDescription: '新的活动提交后会显示在这里。',
      loadFailed: '待审核活动加载失败',
      retry: '重新加载',
      creator: '发起人',
      date: '活动日期',
      page: '第 {page} 页，共 {total} 项'
    },
    agent: {
      title: 'ZVAgent 执行结果',
      description: '最近的后台执行记录',
      empty: '暂无执行记录',
      emptyDescription: '你发起的 ZVAgent 执行结果会显示在这里。',
      loadFailed: 'ZVAgent 执行结果加载失败',
      retry: '重新加载',
      rounds: '{count} 轮',
      statuses: {
        queued: '排队中',
        running: '执行中',
        succeeded: '已完成',
        failed: '失败',
        cancelled: '已取消',
        interrupted: '已中断'
      },
      stopReasons: {
        completed: '正常完成',
        max_iterations: '达到轮数上限',
        token_budget: '达到 Token 上限',
        timeout: '执行超时',
        cancelled: '已取消',
        error: '执行错误'
      }
    },
    suspiciousLogs: {
      title: '可疑日志',
      description: '最近的风险与安全告警',
      empty: '暂无可疑日志',
      emptyDescription: '当前没有需要关注的风险事件。',
      loadFailed: '可疑日志加载失败',
      retry: '重新加载',
      viewAll: '查看日志'
    }
  }
}

export const zhCN = {
  merge: {
    title: '合并义工',
    form: {
      name: '名称',
      type: '类型',
      list: '列表',
      prompt: '注意：只有类型相同且有效的义工才能被合并。',
      overwrite: '覆盖时间',
      overwriteInfo:
        '如果启用此选项，如果待合并的义工中存在重复用户，其结果时间将是现有义工中的最大时间；否则将是总和。',
      overwritePrompt: {
        yes: '是',
        no: '否'
      },
      action: '合并'
    }
  },
  groupList: {
    title: '群组列表',
    columns: {
      name: '名称',
      permission: '权限',
      details: '详细信息',
      search: '搜索'
    }
  },
  groupDetails: {
    tabs: {
      users: '用户',
      activities: '义工',
      time: '时间',
      statistics: '统计',
      create: '新增'
    },
    userList: {
      checkPasswordPrompt: '查看密码修改情况',
      columns: {
        name: '姓名',
        id: '学号',
        group: '群组',
        classid: '班级',
        permission: '权限',
        pwdm: '密码修改情况',
        details: '详细信息',
        status: '状态',
        pwdmStatus: {
          true: '已修改',
          false: '未修改'
        },
        remove: '删除',
        submit: '提交'
      },
      open: '进入个人详细界面'
    },
    timeList: {
      diff: '查看与达标所差时间',
      filled: '已完成',
      diffed: '还差 {time} 小时'
    },
    statistics: {
      chartType: {
        doughnut: '环形图',
        bar: '柱状图'
      },
      chartSwitch: '图表类型切换',
      ariaBar: '班级达标人数柱状分布图',
      ariaDoughnut: '{mode}达标人数环形分布图',
      noPermission: '无权限查看班级统计',
      loadFailed: '统计加载失败，请稍后重试',
      empty: '暂无统计数据',
      modeEmpty: '该类型暂无记录',
      completed: '已达标',
      total: '总人数',
      completedOf: '已达标 {count} 人',
      rateOf: '达标率 {rate}%',
      bucketLine: '{count} 人（{percent}%）',
      sortedBarNote: '各区间已按三类合计人数从少到多排序',
      viewRoster: '点击查看成员名单',
      rosterUnavailable: '成员名单暂未开放',
      rosterEmpty: '该区间暂无成员',
      barPersonLine: '{mode}：{count} 人'
    }
  },
  personalPanel: {
    tabs: {
      info: '信息',
      activity: '义工',
      modify: '修改',
      logs: '日志'
    },
    resetPassword: '重置该用户密码',
    resetConfirm: '请确认您的操作。',
    initialPasswordDialog: {
      title: '一次性初始密码',
      notice: '请立即复制并转交给该用户。此密码只显示这一次，用户首次登录后必须修改密码。',
      studentNumberNotice: '该账号的初始密码为学号，请提醒该生首次登录后立即修改密码。',
      copy: '复制',
      copied: '已复制',
      acknowledge: '我已记下'
    },
    userCreatedTitle: '用户已创建',
    notFound: '无此用户',
    bannedTag: '已封禁',
    ban: '封禁账号',
    unban: '解封账号',
    banSelfForbidden: '不能封禁自己',
    banConfirm: '确定要封禁用户 {name} 吗？封禁后该用户将无法登录。',
    unbanConfirm: '确定要解封用户 {name} 吗？',
    bannedNotification: '已封禁用户 {name}',
    unbannedNotification: '已解封用户 {name}'
  },
  upload: {
    label: '上传',
    requirement:
      'xls/xlsx 文件，大小不超过 5 MiB。您应保证文件是由提供的模板导出的，否则服务器将拒绝它。',
    prompt: {
      black: '拖拽文件至此或',
      em: '点击上传'
    }
  },
  manage: {
    tabs: {
      groups: '群组',
      users: '用户',
      time: '时间',
      import: '导入',
      logs: '日志'
    }
  },
  exports: {
    title: '导出数据',
    range: {
      label: '范围',
      start: '开始日期',
      to: '至',
      end: '结束日期'
    },
    name: '文件名',
    format: '格式',
    cache: '缓存',
    cacheFields: {
      enabled: '启用缓存',
      disabled: '重新计算'
    },
    desc: '描述',
    descFields: {
      enabled: '包含描述',
      disabled: '不包含描述'
    },
    formats: {
      csv: 'CSV (逗号分隔值)',
      json: 'JSON (JavaScript 对象表示法)',
      excel: 'Excel (OpenXML)',
      html: 'HTML (超文本标记语言)',
      latex: 'LaTeX (Leslie Lamport TeX)'
    },
    actions: {
      export: '导出',
      download: '下载'
    },
    prompt: '导出任务 ID 为 {task}。确认您的进度，当任务完成时点击下方「下载」按钮。'
  }
}

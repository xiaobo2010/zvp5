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
    createdNotification: '用户创建成功',
    modifiedNotification: '用户已修改',
    reloginNotice: '身份已修改：该用户需重新登录才能继续使用。',
    studentIdInvalid: '学号格式不正确：必须为 8 位数字',
    roles: {
      title: '身份',
      student: '学生',
      secretary: '团支书',
      department: '学生会',
      admin: '管理员',
      teacher: '教师'
    },
    deleteUser: '删除用户',
    deleteSelfForbidden: '不能删除自己',
    deleteConfirm:
      '确定要删除用户 {name} 吗？此操作不可恢复，删除后 30 天内可由管理员从回收站恢复。',
    deletedNotification: '已删除用户 {name}，30 天内可从回收站恢复',
    notFound: '无此用户',
    bannedTag: '已封禁',
    ban: '封禁账号',
    unban: '解封账号',
    banSelfForbidden: '不能封禁自己',
    banConfirm: '确定要封禁用户 {name} 吗？封禁后该用户将无法登录。',
    unbanConfirm: '确定要解封用户 {name} 吗？',
    bannedNotification: '已封禁用户 {name}',
    unbannedNotification: '已解封用户 {name}',
    banDialogTitle: '封禁账号',
    banDialogNotice: '管理员封禁为永久封禁；解封需管理员手动操作。',
    banReasonLabel: '封禁原因',
    banReasonPlaceholder: '例如：同学号多设备登录',
    banReasonInvalid: '请填写 1-200 字的封禁原因',
    banReasonFallback: '管理员封禁',
    banDialogConfirm: '确认封禁',
    banPermanent: '永久',
    banUntil: '到期：{time}'
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
      import: '导入',
      notifications: '通知',
      ban: '封禁',
      logs: '日志'
    }
  },
  notifications: {
    title: '通知管理',
    subtitle: '创建后立即发送给所有已登录用户；用户端按本地已阅状态逐条提醒。',
    create: '撰写通知',
    refresh: '刷新',
    edit: '编辑',
    activate: '恢复',
    deactivate: '停用',
    delete: '删除',
    cancel: '取消',
    empty: '还没有通知',
    loadFailed: '加载失败，请稍后重试',
    deleteHint: '如需保留发送记录，请优先停用；删除为不可恢复的硬删除。',
    createdNotice: '通知已发送',
    savedNotice: '通知已更新',
    activatedNotice: '通知已恢复发送',
    deactivatedNotice: '通知已停用',
    deletedNotice: '通知已删除',
    noChanges: '没有需要保存的修改',
    sendConfirmTitle: '发送通知',
    sendConfirm: '通知创建后将立即发送给所有用户，是否确认？',
    sendConfirmOk: '确认发送',
    sendConfirmCancel: '返回修改',
    deleteConfirmTitle: '删除通知',
    deleteConfirm: '确定要永久删除「{title}」吗？删除后无法恢复，建议优先使用停用。',
    columns: {
      title: '标题',
      content: '正文预览',
      author: '发布人',
      createdAt: '发布时间',
      status: '状态',
      actions: '操作'
    },
    status: {
      active: '已发布',
      inactive: '已停用'
    },
    dialog: {
      createTitle: '撰写通知',
      editTitle: '编辑通知',
      titleLabel: '标题',
      titlePlaceholder: '例如：关于本周义工活动调整的通知',
      contentLabel: '正文',
      contentPlaceholder: '输入通知正文，支持 Markdown 格式。',
      contentHint: '支持 Markdown；正文最长 4000 字且按 UTF-8 编码不超过 4000 字节（中文约 3 字节/字）。',
      charCount: '{current} / {max} 字',
      byteCount: '{bytes} / {max} 字节',
      createHint: '发送人由服务器按当前登录账号记录，提交前会再次确认。',
      editHint: '该通知最初发送于 {time}；编辑后版本号会递增，用户会再次收到提醒。',
      send: '发送',
      save: '保存修改'
    },
    validation: {
      titleRequired: '请填写通知标题',
      titleTooLong: '标题不能超过 {max} 个字符',
      contentRequired: '请填写通知正文',
      contentTooLong: '正文不能超过 {max} 个字符',
      contentTooManyBytes: '正文按 UTF-8 编码后不能超过 {max} 字节（中文约 3 字节/字）'
    }
  },
  banRules: {
    rulesTitle: '规则列表',
    usersTitle: '当前被封用户',
    refresh: '刷新',
    loadFailed: '加载失败，请稍后重试',
    savedNotice: '已保存',
    saveFailed: '保存失败',
    revertedNotice: '保存失败，已还原',
    rule: {
      columns: {
        name: '规则名称',
        trigger: '触发条件',
        config: '触发参数',
        penalties: '处罚梯度',
        enabled: '启用',
        actions: '操作'
      },
      builtin: '内置',
      triggerText: '{threshold} 次 / {window_hours} 小时',
      penaltyDay: '{days} 天',
      penaltyPermanent: '永久',
      penaltiesHint: '处罚按梯度递进，超出最后一档时按最后一档处罚',
      edit: '编辑',
      create: '新增规则',
      nameLabel: '规则名称',
      nameRequired: '请输入规则名称',
      codeLabel: '规则标识',
      codePlaceholder: '例如 late_homework',
      codeHint: '小写字母开头，仅含小写字母、数字与下划线，3–64 位',
      codeRequired: '请输入规则标识',
      codeFormat: '规则标识格式不正确',
      triggerLabel: '检查项',
      triggerHint: '默认参数：{threshold} 次 / {window_hours} 小时',
      triggerRequired: '请选择检查项',
      delete: '删除',
      deleteConfirm: '确定删除规则「{name}」？此操作不可恢复',
      deletedNotice: '已删除',
      deleteFailed: '删除失败',
      deleteBuiltinDenied: '内置规则不能删除，只能停用',
      createdNotice: '已创建',
      createFailed: '创建失败'
    },
    dialog: {
      title: '编辑规则：{name}',
      thresholdLabel: '触发次数（次）',
      windowLabel: '时间窗（小时）',
      penaltiesLabel: '处罚梯度（天）',
      penaltiesPlaceholder: '逗号分隔的天数，例如 1,2,7（末尾填 0 表示永久）',
      penaltiesHint: '至少 1 项，最多 6 项，按升序填写（重复视为无效）；0 表示永久，只能写在最后一位',
      cancel: '取消',
      save: '保存',
      create: '创建'
    },
    validation: {
      thresholdRequired: '请输入触发次数',
      thresholdRange: '触发次数须为 2 到 20 之间的整数',
      windowRequired: '请输入时间窗小时数',
      windowRange: '时间窗须为 1 到 168 之间的整数',
      penaltiesFormat: '处罚梯度须为逗号分隔的正整数天数，至少 1 项，最多 6 项，且按升序排列、不重复',
      penaltiesPermanentPosition: '0 表示永久封禁，只能出现在最后一位且只能出现一次'
    },
    records: {
      columns: {
        name: '姓名',
        userId: '学号',
        source: '来源',
        reason: '原因',
        startedAt: '开始时间',
        expiresAt: '到期时间',
        operator: '操作人'
      },
      sourceRule: '规则',
      sourceAdmin: '管理员',
      permanent: '永久'
    },
    users: {
      searchPlaceholder: '按姓名或学号搜索',
      search: '搜索',
      columns: {
        name: '姓名',
        userId: '学号',
        className: '班级',
        reason: '原因',
        period: '期限',
        source: '来源'
      },
      sourceAdmin: '管理员封禁',
      sourceRule: '规则：{code}',
      sourceUnknown: '历史封禁（无记录）',
      reasonUnknown: '未记录原因',
      permanent: '永久',
      empty: '当前没有被封禁的用户'
    },
    empty: {
      rules: '暂无规则'
    },
    violations: {
      title: '违规记录',
      searchPlaceholder: '按姓名或学号搜索',
      search: '搜索',
      columns: {
        name: '姓名',
        userId: '学号',
        type: '类型',
        signal: '信号',
        detail: '详情',
        time: '时间'
      },
      signals: {
        iframe: '被 iframe 嵌套',
        bridge: '客户端桥缺失',
        devtools: '调试器痕迹'
      },
      detailUnknown: '无详情',
      empty: '暂无违规记录'
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

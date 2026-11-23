export const zhCN = {
  reauth: {
    cancelled: '已取消密码再认证，本次没有做任何修改。'
  },
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
      search: '搜索',
      action: '操作',
      view: '查看',
      rename: '重命名',
      delete: '删除'
    },
    delete: {
      title: '删除该群组？',
      message: '确定删除群组「{name}」吗？删除后群组成员与权限配置将一并清除，且无法恢复。',
      confirm: '删除',
      cancel: '取消',
      deletedNotice: '已删除群组'
    },
    create: {
      action: '添加班级',
      title: '添加班级',
      name: '班级名称',
      placeholder: '例如：高一（3）班',
      required: '请输入班级名称',
      confirm: '创建',
      cancel: '取消',
      creating: '创建中…',
      createdNotice: '已创建班级「{name}」',
      failed: '创建失败，请稍后重试'
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
      diffed: '还差 {time} 小时',
      loadFailed: '时长列表加载失败，请稍后重试'
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
    resetPasswordFailed: '密码重置失败',
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
    studentIdInvalid: '学号不能为空，且不超过 32 个字符',
    roles: {
      title: '身份',
      student: '学生',
      secretary: '团支书',
      department: '学生会',
      admin: '管理员'
    },
    deleteUser: '删除用户',
    deleteSelfForbidden: '不能删除自己',
    deleteConfirm:
      '确定要删除用户 {name} 吗？此操作不可恢复，删除后 30 天内可由管理员从回收站恢复。',
    deletedNotification: '已删除用户 {name}，30 天内可从回收站恢复',
    deleteFailed: '删除用户失败',
    deleteVerifyReason:
      '删除账号会让该用户立即失去全部数据访问权，请输入你自己的密码确认这是管理员本人的操作。',
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
    banUntil: '到期：{time}',
    tempPassword: {
      action: '签发临时密码',
      confirmTitle: '签发临时密码',
      confirm:
        '确认为 {name} 签发一次性临时密码吗？密码 30 分钟内有效，签发后该用户此前的临时密码立即作废，明文只显示这一次。',
      confirmButton: '签发',
      dialogTitle: '一次性临时密码',
      notice: '请立即复制并转达给该用户。此密码只显示这一次，30 分钟内有效，只能在一台设备上使用一次。',
      expiresAt: '有效期至：{time}',
      copy: '复制',
      copied: '已复制',
      acknowledge: '已转达',
      failed: '签发临时密码失败',
      verifyReason:
        '临时密码能直接登录目标账号，属于最高危操作，请输入你自己的密码确认身份。'
    },
    clearDevice: {
      action: '清除主设备',
      title: '清除主设备',
      confirm:
        '确定要清除 {name} 的主学海设备绑定吗？这是换机或丢失旧平板时的人工出口，清除后绑定立即解除，该用户下次登录会被要求重新绑定。',
      confirmButton: '清除',
      done: '已清除 {name} 的主设备绑定',
      failed: '清除主设备失败',
      verifyReason:
        '清除他人主设备等同于帮对方换机，请输入你自己的密码确认这是管理员本人的操作。'
    }
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
    back: '返回',
    tabsLabel: '平台管理分区',
    tabs: {
      groups: '群组',
      users: '用户',
      roles: '角色',
      import: '导入',
      notifications: '通知',
      themes: '主题',
      ban: '封禁管理',
      deviceBans: '设备封禁',
      attachments: '附件管理',
      logs: '日志',
      settings: '设置'
    }
  },
  settings: {
    title: '系统设置',
    subtitle: '这里修改的参数保存后立即生效，不需要重启服务。',
    reauthReason: '修改系统设置属于高危操作，请输入密码完成再认证。',
    refresh: '刷新',
    save: '保存改动',
    discard: '放弃改动',
    saved: '设置已保存',
    saveFailed: '保存失败，请稍后重试',
    loadFailed: '设置加载失败，请稍后重试',
    empty: '后端还没有提供可在线修改的设置项',
    pending: '待保存',
    pendingCount: '共 {count} 项改动待保存',
    itemCount: '{count} 项',
    changed: '已改动，默认 {value}',
    on: '开',
    off: '关',
    emptyValue: '（空）',
    range: '取值范围 {min} ~ {max}',
    updatedAt: '{time} 由 {actor} 修改',
    updatedAtOnly: '{time} 修改',
    updatedBy: '由 {actor} 修改',
    groups: {
      ai: 'AI 网关',
      ops: '运维阈值',
      other: '其他设置'
    },
    confirm: {
      title: '保存设置改动',
      summary: '共 {count} 项改动，保存后立即生效：',
      ok: '确认保存',
      cancel: '取消'
    },
    roleQuota: {
      role: '角色',
      actions: '操作',
      addRow: '添加角色',
      removeRow: '删除该行',
      empty: '还没有角色额度行',
      kinds: {
        ocr: 'OCR 识别',
        chat: '对话'
      }
    },
    flatDict: {
      empty: '后端还没有下发可调的能力项'
    },
    roles: {
      student: '学生',
      secretary: '团支书',
      department: '学生会',
      admin: '管理员'
    }
  },
  roleManagement: {
    title: '角色管理',
    subtitle: '按角色筛选用户并调整身份；不勾选任何角色即为普通学生。',
    filterLabel: '角色筛选',
    filterAll: '全部用户',
    searchPlaceholder: '搜索姓名或学号',
    search: '搜索',
    refresh: '刷新',
    loading: '加载中',
    empty: '没有符合条件的用户',
    loadFailed: '加载失败，请稍后重试',
    columns: {
      name: '姓名',
      id: '学号',
      group: '班级',
      roles: '身份',
      actions: '操作'
    },
    edit: '修改角色',
    cannotEdit: '不能修改自己或管理员账号的角色',
    dialogTitle: '修改角色{name}',
    dialogHint: '不勾选任何角色则为普通学生。',
    save: '保存',
    saving: '保存中',
    cancel: '取消',
    saved: '角色已更新',
    saveFailed: '角色更新失败，请稍后重试',
    noPermission: '您没有权限查看此页面',
    roles: {
      student: '学生',
      secretary: '团支书',
      department: '学生会',
      admin: '管理员'
    }
  },
  rolePolicies: {
    tabsLabel: '角色管理分区',
    tabs: {
      policy: '角色策略',
      users: '用户角色'
    },
    title: '角色策略',
    subtitle: '按角色控制功能开关与月度配额；保存时需要另一位管理员的动态码确认。',
    refresh: '刷新',
    save: '保存',
    saving: '保存中',
    loading: '策略加载中…',
    empty: '后端还没有提供可配置的角色策略',
    loadFailed: '策略加载失败，请稍后重试',
    saveFailed: '保存失败，请稍后重试',
    forbidden: '没有权限修改角色策略（仅管理员可用）',
    notFound: '角色不存在或策略接口尚未开放，请刷新后重试',
    unauthorized: '登录已过期，请重新登录后重试',
    saved: '角色策略已保存',
    pending: '有未保存的改动',
    adminLocked: '管理员固定全量权限',
    readOnly: '该角色不可编辑，如需调整请联系管理员',
    guestRole: '未知角色',
    quotaPlaceholder: '留空用默认',
    authUnavailable: '授权弹窗不可用，请刷新页面后重试',
    authScene: '保存「{role}」的角色策略',
    unsavedTitle: '切换角色',
    unsavedConfirm:
      '当前角色有未保存的改动，切换后这些改动不会保存到服务器（草稿仍保留在本次会话中）。确定切换角色？',
    unsavedOk: '切换',
    unsavedCancel: '留在此页',
    updatedAt: '最后修改：{time}',
    updatedBy: '由 {actor} 修改',
    sections: {
      features: '功能权限',
      featuresHint: '关闭后该角色看不到对应入口，接口也会拒绝这类请求。',
      quotas: '月度配额',
      quotasHint: '0 表示不开放，留空表示沿用系统默认；两者含义不同，不要混用。'
    },
    featureGroups: {
      ai: 'AI 能力',
      activity: '活动与数据',
      ops: '运维只读'
    },
    features: {
      ai: {
        chat: {
          label: 'LLM 对话',
          hint: '允许在 ZVAgent 里发起多轮对话'
        },
        ocr: {
          label: '文字识别(OCR)',
          hint: '允许上传图片做文字识别与取词'
        },
        agent: {
          label: 'ZVAgent',
          hint: '允许使用可自主调用工具的 ZVAgent 模式'
        },
        schedule: {
          label: 'AI 定时任务',
          hint: '允许创建按时间自动执行的 AI 任务'
        },
        keys: {
          label: '自助 API Key',
          hint: '允许自己创建与吊销 API Key'
        }
      },
      activity: {
        stats: {
          label: '活动统计',
          hint: '查看活动出勤与达标统计'
        }
      },
      attachment: {
        manage: {
          label: '附件审核',
          hint: '审核学生上传的证明材料与 OCR 结果'
        }
      },
      export: {
        time: {
          label: '工时导出',
          hint: '导出工时明细为 CSV / Excel'
        }
      },
      import: {
        activity: {
          label: '活动导入',
          hint: '从模板批量导入活动记录'
        }
      },
      logs: {
        read: {
          label: '业务日志查看',
          hint: '查看操作日志与审计记录'
        }
      },
      notify: {
        publish: {
          label: '通知发布',
          hint: '向全站用户发布通知'
        }
      },
      ban: {
        read: {
          label: '封禁名单查看',
          hint: '查看封禁名单与违规记录'
        }
      },
      quota: {
        manage: {
          label: '用户配额管理',
          hint: '调整单个用户的 AI 配额'
        }
      }
    },
    quotas: {
      af: {
        label: 'AF 额度',
        hint: '每月统一额度：对话与 OCR 记在同一个池子'
      },
      schedule: {
        label: 'AI 定时任务条数',
        hint: '当前规则：学生会 1 条；0 表示不允许创建'
      }
    },
    roles: {
      student: '学生',
      secretary: '团支书',
      department: '学生会',
      admin: '管理员'
    }
  },
  notifications: {
    title: '通知管理',
    subtitle: '创建后立即发送给所有已登录用户；用户端按本地已阅状态逐条提醒。',
    reauthReason: '发布或修改全站通知属于高危操作，请输入密码完成再认证。',
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
    },
    history: {
      reauthReason: '回滚通知版本属于高危操作，请输入密码完成再认证。',
      entry: '历史',
      title: '修改历史',
      empty: '暂无历史版本',
      notFound: '通知不存在或已被删除',
      forbidden: '没有权限查看修改历史（仅管理员可用）',
      network: '网络异常，无法加载修改历史',
      unknown: '加载修改历史失败',
      compareOn: '对比当前',
      compareOff: '查看原文',
      identical: '与当前版本一致',
      collapsedLines: '已折叠 {count} 行无变化内容',
      ops: {
        create: '新建',
        update: '编辑',
        delete: '删除',
        rollback: '回滚',
        rollbackFrom: '回滚到 v{version}'
      },
      rollback: '回滚',
      rollbackTo: '回滚到 v{version}',
      rollbackHint: '回滚会生成一个新版本，历史记录保持不变。',
      rollbackConfirmTitle: '确认回滚',
      rollbackConfirm:
        '将把通知内容恢复到 v{version} 的状态。回滚会产生一个新版本，不会改写历史记录。确认回滚？',
      rollbackConfirmOk: '确认回滚',
      rollbackDone: '已回滚，新版本为 v{version}',
      rollbackForbidden: '没有权限执行回滚（仅管理员可用）',
      rollbackMissing: '通知或目标版本不存在，请刷新后重试',
      rollbackNetwork: '网络异常，回滚未完成，请稍后重试',
      rollbackFailed: '回滚失败，请稍后重试'
    },
    changelog: {
      title: '版本更新',
      hint: '新增或修改版本条目后，会自动以 SYSTEM 名义向用户下发版本公告通知。',
      reauthReason: '修改版本更新记录会向全站下发公告，请输入密码完成再认证。',
      add: '新增版本',
      empty: '暂无版本记录',
      columns: {
        version: '版本号',
        date: '发布日期',
        items: '更新条目',
        actions: '操作'
      },
      moreItems: '等 {count} 条',
      dialog: {
        createTitle: '新增版本',
        editTitle: '编辑版本',
        version: '版本号',
        versionPlaceholder: '例如: v4.3.0-6',
        date: '发布日期',
        items: '更新条目',
        itemsHint: '每行一条',
        save: '保存'
      },
      deleteConfirm: '删除后该版本公告将同时从用户通知中撤下，确定删除？',
      saved: '版本已保存，公告已自动下发',
      deleted: '版本已删除',
      saveFailed: '保存失败，请重试'
    }
  },
  banRules: {
    rulesTitle: '规则列表',
    usersTitle: '当前被封用户',
    reauthReason: '修改封禁规则属于高危操作，请输入密码完成再认证。',
    refresh: '刷新',
    loadFailed: '加载失败，请稍后重试',
    savedNotice: '已保存',
    saveFailed: '保存失败',
    revertedNotice: '保存失败，已还原',
    sections: {
      label: '封禁管理分区',
      rules: '规则与违规',
      accounts: '账号封禁',
      devices: '设备封禁'
    },
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
        devtools: '调试器痕迹',
        agent_jailbreak: 'agent 越狱/提示词注入'
      },
      detailUnknown: '无详情',
      empty: '暂无违规记录'
    }
  },
  deviceBans: {
    hint: '设备封禁按设备号生效，与账号封禁是两份独立记录：解封只放出设备，账号封禁不会随之解除。重置梯度只清零该设备的违规次数，下一次违规从第一档重新计。',
    reauthReason: '封禁或解封设备属于高危操作，请输入密码完成再认证。',
    refresh: '刷新',
    loadFailed: '加载失败，请稍后重试',
    cancel: '取消',
    manual: {
      title: '手动封禁',
      xuehaiIdLabel: '设备号',
      xuehaiIdPlaceholder: '请输入 1–32 位数字设备号',
      xuehaiIdInvalid: '设备号须为 1 到 32 位数字',
      reasonLabel: '封禁原因',
      reasonPlaceholder: '例如：使用他人设备代刷考勤',
      reasonRequired: '请填写封禁原因',
      durationLabel: '封禁时长',
      durations: {
        day1: '1 天',
        day7: '7 天',
        day30: '30 天',
        forever: '永久'
      },
      submit: '确认封禁',
      confirmTitle: '封禁设备',
      confirm: '确定要封禁设备 {xuehaiId}（{period}）吗？封禁后该设备将无法登录。',
      confirmButton: '封禁',
      bannedNotice: '已封禁设备 {xuehaiId}',
      banFailed: '封禁失败，请稍后重试'
    },
    table: {
      title: '已封禁设备',
      searchPlaceholder: '按设备号搜索',
      search: '搜索',
      columns: {
        xuehaiId: '设备号',
        reason: '封禁原因',
        source: '来源',
        ruleCode: '规则',
        until: '封禁至',
        banCount: '梯度计数',
        owner: '所属用户',
        createdAt: '封禁时间',
        actions: '操作'
      },
      sourceAdmin: '管理员',
      sourceRule: '规则',
      permanent: '永久',
      reasonUnknown: '未记录原因',
      empty: '当前没有被封禁的设备',
      unban: '解封',
      unbanConfirmTitle: '解封设备',
      unbanConfirm: '确定要解除设备 {xuehaiId} 的封禁吗？解除后该设备可以立即登录。',
      unbanConfirmButton: '解封',
      unbannedNotice: '已解封设备 {xuehaiId}',
      unbanFailed: '解封失败，请稍后重试',
      reset: '重置梯度',
      resetConfirmTitle: '重置违规梯度',
      resetConfirm: '确定要重置设备 {xuehaiId} 的违规梯度吗？重置后该设备下次违规从第一档重新计。',
      resetConfirmButton: '重置',
      resetNotice: '已重置设备 {xuehaiId} 的违规梯度',
      resetFailed: '重置失败，请稍后重试'
    }
  },
  attachments: {
    title: '附件管理',
    subtitle:
      '核查学生上传的附件：看原图与 OCR 文本，再决定通过还是驳回。裁决与删除都会写进审计时间线。',
    refresh: '刷新',
    resetFilters: '清空筛选',
    filters: '筛选',
    filterHint:
      '默认只列原图：缩略图与派生文件是原图的附属行，需要逐条核对时可在「类型」里放开。',
    cancel: '取消',
    loadFailed: '加载失败，请稍后重试',
    empty: '当前没有符合条件的附件',
    verifyReason: '查看与审计附件属于高危操作，请输入密码完成再认证。',
    verifyRequired: '需要密码验证',
    verifyIntro:
      '附件里是学生证件与身份材料，进入审计页前需要再输入一次密码。验证 5 分钟内有效，过期后下一次裁决或删除会再问一次。',
    verifyRetry: '重新验证',
    deviceUnlockReason: '平板访问附件需要验证密码，解锁后 5 分钟内有效。',
    deviceUnlockRequired: '当前设备尚未解锁，请完成设备验证后重试。',
    deleteVerifyReason: '删除附件前需要再输入一次密码确认。',
    filterFields: {
      purpose: '用途',
      purposeAll: '全部用途',
      status: '状态',
      statusAll: '全部状态',
      ocrStatus: 'OCR 状态',
      ocrStatusAll: '全部 OCR 状态',
      kind: '类型',
      kindAll: '全部类型',
      owner: '上传者',
      ownerAll: '全部上传者',
      range: '上传时间',
      rangeTo: '至',
      rangeStart: '开始时间',
      rangeEnd: '结束时间'
    },
    status: {
      stored: '已存储',
      processing: '处理中',
      ready: '就绪',
      verified: '已通过',
      rejected: '已驳回',
      quarantined: '已隔离',
      deleted: '已删除'
    },
    ocrStatus: {
      pending: '待识别',
      processing: '识别中',
      done: '已完成',
      failed: '失败'
    },
    kind: {
      original: '原图',
      thumb: '缩略图',
      derived: '派生文件'
    },
    purpose: {
      volunteer_order: '义工登记单'
    },
    eventActions: {
      upload: '上传',
      audit_verified: '裁决通过',
      audit_rejected: '裁决驳回',
      verify: '查证',
      bind: '绑定对象',
      link: '签发外链',
      delete: '删除',
      ocr_start: '开始识别',
      ocr_done: '识别完成',
      ocr_failed: '识别失败'
    },
    columns: {
      thumbnail: '缩略图',
      file: '文件',
      purpose: '用途',
      binding: '绑定对象',
      status: '状态',
      owner: '上传者',
      createdAt: '上传时间'
    },
    table: {
      title: '附件列表',
      count: '共 {count} 条',
      unbound: '未绑定',
      view: '查看',
      decide: '裁决',
      delete: '删除'
    },
    detail: {
      title: '附件详情',
      retry: '重试',
      loadFailed: '详情加载失败，请稍后重试',
      original: '原图',
      imageLoading: '原图加载中…',
      imageFailed: '原图加载失败，可能是文件已被清理。',
      ocrText: 'OCR 文本',
      noOcr: '该附件没有 OCR 结果',
      lines: '识别行',
      noLines: '没有可逐行对照的识别结果',
      noBox: '无坐标',
      events: '审计时间线',
      noEvents: '暂无审计记录',
      meta: {
        owner: '上传者',
        mime: '类型',
        size: '大小',
        dimensions: '尺寸',
        purpose: '用途',
        status: '状态',
        createdAt: '上传时间'
      },
      notePlaceholder: '裁决备注（可选）',
      approve: '通过',
      reject: '驳回',
      rejectConfirmTitle: '驳回附件',
      rejectConfirm: '确定要驳回该附件吗？驳回后附件不再对外可用。',
      approved: '已通过该附件',
      rejected: '已驳回该附件',
      decideFailed: '裁决失败，请稍后重试'
    },
    delete: {
      confirmTitle: '删除附件',
      confirm: '确定要删除附件「{name}」吗？删除后该附件不再可读。',
      confirmButton: '删除',
      deleted: '已删除附件',
      failed: '删除失败，请稍后重试'
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
    createFailed: '创建导出任务失败，请稍后重试',
    prompt: '导出任务 ID 为 {task}。确认您的进度，当任务完成时点击下方「下载」按钮。'
  },
  themes: {
    title: '主题统计',
    subtitle: '按账号当前选择的主题统计使用分布',
    refresh: '刷新',
    loadFailed: '加载失败，请稍后重试',
    noPermission: '没有权限查看主题统计（仅管理员可用）',
    empty: '暂无主题数据',
    total: '总账号数',
    theme: '主题',
    count: '使用人数',
    percent: '占比',
    other: '未识别'
  }
}

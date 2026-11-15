export const zhCN = {
  create: {
    normal: '录入义工',
    special: '特殊录入',
    merge: '合并义工',
    upload: '上传数据'
  },
  createMinimized: {
    normal: '录入',
    special: '特殊',
    merge: '合并',
    upload: '上传'
  },
  mode: {
    'on-campus': {
      name: '校内义工',
      short: '校内义工',
      key: 'on-campus'
    },
    'off-campus': {
      name: '校外义工',
      short: '校外义工',
      key: 'off-campus'
    },
    'social-practice': {
      name: '社会实践',
      short: '社会实践',
      key: 'social-practice'
    },
    hybrid: {
      name: '特殊义工',
      short: '特殊',
      key: 'hybrid'
    }
  },
  type: {
    unknown: '未知',
    specified: '指定义工',
    special: '特殊义工',
    social: '社会义工',
    scale: '大型义工'
  },
  form: {
    type: '类型',
    title: '{create} {type}',
    name: '名称',
    description: '描述',
    descriptionPlaceholder: '请输入义工描述，支持 Markdown 语法',
    date: '日期',
    mode: '模式',
    person: '成员',
    class: '班级',
    duration: '时长',
    classify: '分类',
    pending: '等待审核',
    detail: '详细信息',
    unnamed: '未命名',
    actions: {
      submit: '提交',
      cancel: '取消',
      modify: '修改',
      save: '保存',
      reset: '重置',
      delete: {
        name: '删除',
        confirm: '删除后数据皆不可恢复，确定删除？'
      },
      modification: {
        status: '修改义工 {activity} 的状态',
        effective: '批准',
        refused: '拒绝',
        pending: '打回重审',
        confirm: '请确认对义工状态的修改'
      },
      refresh: '刷新'
    },
    upload: {
      name: '上传',
      prompt: '点击或拖拽文件到此区域上传',
      allow: '仅允许 {type} 文件'
    },
    details: '详情',
    saveFailed: '保存失败，请重试',
    volunteerOrder: {
      name: '义工单',
      needCreateInfo: '请先完成基本信息，再上传义工单',
      noPermission: '您没有管理义工单的权限',
      deviceUnsupported: '学海设备不支持管理义工单，请使用其他设备操作',
      unlockRequired: '平板访问义工单需要验证密码，已取消验证',
      unlockRetry: '重新验证',
      loadFailed: '义工单加载失败，请重试'
    }
  },
  statistics: {
    name: '统计',
    refresh: '强制刷新统计',
    export: '导出统计',
    exportDone: '已导出',
    empty: '暂无统计数据',
    loadFailed: '统计数据加载失败，请稍后重试',
    retry: '重试',
    sections: {
      overview: '概览',
      distribution: '时长分布',
      mode: '参与模式',
      classes: '班级对比'
    },
    metrics: {
      total: '有效参与人数',
      sum: '总时长',
      mean: '平均值',
      median: '中位数',
      min: '最小值',
      max: '最大值',
      p25: '25% 分位数',
      p75: '75% 分位数',
      mode: '众数',
      std: '标准差'
    },
    distribution: {
      title: '时长分布',
      aria: '活动参与时长分布柱状图',
      axisPerson: '人数',
      axisHours: '时长（小时）',
      percentile: '25% / 50% / 75% 分位：{p25} / {p50} / {p75} 小时',
      binLine: '分箱宽度 {width} 小时 · 峰值区间 {range}（{count} 人）'
    },
    groups: {
      mode: '参与模式',
      class: '班级',
      ariaMode: '活动参与模式占比环形图',
      ariaClass: '活动班级总时长对比横向柱状图（按总时长从少到多，未分班垫底）',
      unnamedClass: '未分班',
      unknownClass: '未知班级',
      unknownClassWithId: '未知班级（{id}）',
      count: '人数',
      sum: '总时长',
      mean: '人均时长',
      axisSum: '总时长（小时）',
      sortedAsc: '全部班级已按总时长从少到多排列，未分班固定在末尾',
      rosterToggle: '展开成员名单',
      rosterUnavailable: '成员名单暂未开放'
    },
    csv: {
      overview: '概览',
      metric: '指标',
      value: '数值',
      distribution: '时长分布',
      range: '区间',
      modeGroup: '分组-模式',
      classGroup: '分组-班级'
    }
  },
  units: {
    item: '项',
    hour: '小时',
    person: '人'
  },
  impression: {
    name: '记录',
    actions: {
      view: '查看'
    }
  },
  registration: {
    location: '地点',
    approver: '审批人',
    approvers: {
      authority: '学校团委',
      member: '部门成员'
    },
    status: {
      title: '状态'
    }
  },
  register: {
    pendingRecordNotice: '该活动仍在审核中，提交的成员时长将在审核通过后计入统计',
    blockedRecordNotice: '该活动已驳回或未提交审核，无法录入成员',
    pendingSummary: '审核中活动的成员不计入时长统计，直到审核通过',
    overrideNotice: '已录入成员修改时长后提交将直接覆盖，不会叠加',
    existingBadge: '已录入',
    existingHint: '已在活动中，现有时长 {duration} 小时，提交将覆盖',
    existingHintUnchanged: '与现有时长一致，提交时仍会按本行覆盖',
    existingLoaded: '已载入 {count} 名已在活动中的成员',
    existingLoading: '载入已在活动中的成员…',
    existingTruncated: '仅载入前 {limit} 名已有成员，其余命中时提交会再次确认覆盖',
    rowLimitReached: '表格最多 {limit} 行，请先提交或删除部分行后再新增',
    rowLimitTruncated: '已达 {limit} 行上限，本次丢弃 {dropped} 行，剩余部分请分批粘贴或识别',
    existingLoadFailed: '载入已录入成员失败，提交时命中已有记录会再次确认覆盖',
    existingLoadFailedShort: '已录入成员载入失败',
    noPending: '没有待提交的行；已录入成员修改时长后提交会直接覆盖，不会叠加',
    nothingChanged: '已录入成员的时长都没有变化，无需提交',
    updatedRow: '时长已覆盖',
    insertedRow: '已新增',
    updateFailed: '更新失败：{reason}',
    submitSummary: '《{activity}》覆盖 {updated} 条 / 新增 {added} 条',
    submitMixedSummary: '《{activity}》已提交 {count} 条（新增 {inserted} 条，覆盖 {overwritten} 条已有记录）',
    partialFailedTitle: '部分成员更新失败',
    partialFailedSummary:
      '本次更新 {updated} 条、新增 {added} 条，失败 {failed} 条；成功的部分不回滚，可修正后重试',
    // 请求可能已在服务端提交后才失败（超时/断网/5xx）：不能算成功，也不能自动重发。
    submitUnconfirmed: '结果未确认，请刷新成员列表核对后重试',
    submitUnconfirmedSummary:
      '有 {count} 行提交结果未确认（请求超时、网络中断或服务端异常），请刷新成员列表核对后再提交',
    moreFailures: '等共 {count} 条',
    // —— 时长与行校验 ——
    durationRequired: '请填写时长',
    durationOutOfRange: '时长需大于 0 且不超过 25',
    duplicateInTable: '名单内重复，提交时以靠后一行覆盖',
    duplicateConfirmTitle: '名单内有重复录入',
    duplicateConfirmDesc:
      '有 {count} 人重复录入（{names}）。继续提交将用靠后一行的时长和备注覆盖前面的行。',
    duplicateConfirmAction: '覆盖并提交',
    crossTabDuplicate: '「{name}」（{mode}）同时出现在两个栏目里，请删掉其中一栏的行后再提交',
    conflictConfirmTitle: '发现未载入的已录入成员',
    conflictConfirmDesc:
      '服务器发现这批 {count} 人里有已录入记录（本地索引未覆盖）。继续将按本次填写的时长覆盖，不会累加。',
    conflictSkipped: '已取消：这批新成员未写入，请刷新活动成员后重试',
    newMembersLabel: '新增成员',
    searchPlaceholder: '输入姓名或学号',
    searchFailed: '搜索已录入成员失败，请稍后重试',
    searchTruncated: '结果超过 200 条，仅显示前 200 条，请输入更精确的关键词',
    notInActivity: '该成员不在本活动中，请到「新增义工」栏登记',
    otherModeRecorded: '该成员已在其他模式登记，请确认本次模式无误',
    alreadyInGrid: '该成员已在表格中',
    invalidRowsRemain: '还有 {count} 行需要修正后再提交',
    activityRequired: '请先选择目标义工活动',
    // —— 覆盖确认（提交是设置时长，不是累加）——
    overwriteConfirmTitle: '确认覆盖 {count} 条已有记录？',
    overwriteConfirmDesc: '提交是设置时长，不会累加：同一人的时长会被这次填写的值替换。',
    overwriteTruncatedNotice:
      '已有成员索引超过 {limit} 条被截断，部分成员没有列出；本次提交仍按设置时长写入，不会累加。',
    overwriteColName: '姓名',
    overwriteColStudentId: '学号',
    overwriteColOldDuration: '原时长',
    overwriteColNewDuration: '新时长',
    overwriteColAction: '动作',
    overwriteAction: '覆盖',
    overwriteSkipAction: '覆盖（时长未变）',
    confirmOverwrite: '确认覆盖',
    cancelAction: '取消',
    deleteAction: '删除',
    ocrAction: 'OCR 识别',
    // —— OCR 结果确认（图片框选 + 文字校对 + 换引擎重识别）——
    ocrEngineLabel: '识别引擎',
    ocrAutoOption: '自动（按站点优先级回退）',
    ocrReviewTitle: '识别结果确认',
    ocrReviewRerun: '重新识别',
    ocrReviewApply: '确认填入登记表',
    ocrReviewImageTitle: '登记表原图（点击框选可排除该行）',
    ocrReviewImageAlt: '登记表原图',
    ocrReviewImageLoading: '正在载入图片…',
    ocrReviewImageFailed: '图片载入失败，可继续在右侧核对文字',
    ocrReviewLinesTitle: '识别文字（可直接修改）',
    ocrReviewPreviewTitle: '解析预览',
    ocrReviewNoBox: '本次引擎没有返回文字坐标，无法在图片上框选，请在右侧逐行核对文字',
    ocrReviewNoText: '没有识别到文字，可换一个引擎重试，或取消后手动录入',
    ocrReviewSelected: '已选 {count} 行',
    ocrReviewSelectAll: '全选',
    ocrReviewClearAll: '全不选',
    ocrReviewEmpty: '没有勾选可解析的文字，请至少保留一行',
    ocrReviewRerunFailed: '重新识别失败：{reason}',
    // —— 提交限制与索引失败 ——
    submitLimitExceeded: '一次最多提交 {limit} 人，当前 {count} 人，请分批提交',
    submitUnindexedNotice:
      '已录入成员索引仅载入前 {limit} 名，未载入部分若有重复会整批被拒，届时可再确认覆盖',
    retryLoad: '重试',
    // —— 行删除确认 ——
    removeRowTitle: '删除该行？',
    removeRowConfirm: '「{name}」这一行的内容将不再提交，删除后可用「撤回」恢复。',
    // —— 撤回 / 重做 ——
    undo: '撤回',
    redo: '重做',
    savedRow: '已提交',
    // —— 顶部提示（可收起）——
    noticeTrigger: '提示',
    noticeNeverShow: '不再提示',
    noticeListTitle: '本地学生名单未就绪',
    noticeListDesc:
      '点击右上角“同步名单”拉取一次全校学生索引；之后补全只在本地 index.db 内完成，不会逐格查询数据库。',
    noticeListShort: '本地学生名单未就绪，请先同步名单',
    noticeTypeTitle: '活动类型为{type}，明细行模式已锁定',
    noticeTypeDesc: '该活动不是特殊义工，明细行的模式列已锁定为该类型。',
    noticeOcrTitle: 'OCR 识别会消耗 AI 网关额度',
    noticeOcrDesc:
      '仅支持 JPG / PNG / WebP，单张不超过 15MB。识别文本会在本地拆行并用 index.db 校正，提交前请逐行复核。'
  },
  special: {
    classify: {
      name: '分类',
      prize: '获奖',
      import: '导入',
      club: '团体',
      other: '其他'
    }
  },
  status: {
    title: '状态',
    pending: '审核中',
    effective: '有效',
    refused: '无效'
  },
  analysis: {
    pendingNote: '本页有 {count} 项审核中的时长尚未计入累计时长，审核通过后会计入。',
    pendingTip: '审核中的时长尚未计入累计时长，审核通过后会计入。',
    action: 'AI 分析',
    title: '活动分析',
    hint: '参考内容含活动标识、基本信息与聚合统计，不含成员名单',
    partialFailed: '部分资料未能获取，分析参考内容可能不完整'
  },
  member: {
    name: '姓名',
    status: '状态',
    class: '班级',
    dialog: {
      title: '{name}的成员列表',
      actions: {
        name: '操作',
        add: '添加',
        remove: '移除',
        confirm: '移除后将不可恢复，确定移除？',
        removeTitle: '移除该成员？',
        removeMessage: '移除后该成员在本活动中的时长记录将被删除，且不可恢复。',
        cancel: '取消',
        title: '添加成员至{activity}'
      }
    },
    past: {
      removeTitle: '移除该历史成员？',
      removeMessage: '移除后该成员在历史活动中的记录将不再展示，且不可恢复。',
      remove: '移除',
      cancel: '取消'
    },
    userDelete: {
      title: '删除该用户？',
      confirm: '删除',
      cancel: '取消'
    },
    inline: {
      title: '成员列表',
      count: '共 {count} 人',
      empty: '暂无成员'
    },
    review: {
      label: '义工状态',
      title: '修改 {name} 的义工状态',
      effective: '通过',
      pending: '待审核',
      refused: '驳回',
      confirm: '请确认对该成员义工状态的修改',
      success: '义工状态已更新',
      status: {
        effective: '已通过',
        pending: '待审核',
        refused: '已驳回'
      }
    }
  },
  view: {
    loadFailed: '义工详情加载失败，请重试',
    retry: '重试',
    myDurationLoadFailed: '我的时长加载失败，请稍后重试',
    panels: {
      mine: {
        name: '我的义工',
        short: '我的'
      },
      class: {
        name: '班级义工',
        short: '班级'
      },
      campus: {
        name: '全部义工',
        short: '全部'
      }
    }
  },
  export: {
    name: '导出',
    title: '导出数据',
    actions: {
      export: '导出',
      download: '下载'
    }
  },
  batch: {
    batch_import: '批量导入',
    manual: {
      member: '成员',
      mode: '模式',
      duration: '时间'
    },
    batch: {
      classid: '班级',
      members: '成员',
      selected: '已添加 {count} 位成员。',
      mode: '模式',
      duration: '时长'
    },
    or: '或',
    steps: {
      prev: '上一步',
      next: '下一步'
    }
  },
  origins: {
    name: '来源',
    labor: {
      name: '体力劳动',
      description: '学校发起的劳动类活动，通常涉及体力或集体性工作，如搬桌椅、布置考场等。'
    },
    organization: {
      name: '学生组织',
      description:
        '参与由官方学生组织或外部团体组成的组织，通常包括学生会、铜管乐团等或以比赛为导向的社团，如领导力、模拟政协等。'
    },
    tasks: {
      name: '学校任务',
      description: '分配的责任或职责，通常是个人或小组形式，通常包括在考试期间引导学生等角色。'
    },
    occasions: {
      name: '学校活动',
      description:
        '在典礼或纪念活动中担任角色，如十佳选举、校庆、毕业典礼等，包括主持人、引导员等。'
    },
    import: {
      name: '数据导入',
      description: '从旧系统或外部平台手动导入的数据。'
    },
    activities: {
      name: '文体活动',
      description: '文体活动，主要由学校和学生组织共同主办，如运动会裁判、篮球赛等。'
    },
    practice: {
      name: '社会实践',
      description: '以社会实践为基础的活动，通常是校外的，涉及社区服务或类似的贡献。'
    },
    club: {
      name: '社团活动',
      description: '由社团或兴趣小组组织的有益于学校的活动，包括辩论、魔方比赛、音乐会等。'
    },
    prize: {
      name: '获奖义工',
      description:
        '与奖项或荣誉相关的活动，从校级到国际级，具体发放时间根据相关规定，可选择校内或校外计入模式。'
    },
    other: {
      name: '其他义工',
      description: '不属于任何预定义类别的活动。'
    }
  },
  creating_guide: {
    info: {
      next: '点击「下一步」将直接注册义工，成员列表在下一页修改，请确保信息准确完整。'
    },
    check: {
      created: '创建已完成，接下来将对该义工进行审核。',
      allowed:
        '请仔细检查义工信息，如果需要申请表，请链接表单 ID（若有）。表单必须由学生和相关老师签字，姓名完整，最终确认发放时间。',
      disallowed:
        '您没有权限审核此义工。它将由学生会的负责成员进行评估。请耐心等待，审核完成后会通知您。'
    },
    print: '您可以通过点击「打印」按钮打印系统生成的申请表，但我们建议使用官方模板并手动填写。'
  }
}

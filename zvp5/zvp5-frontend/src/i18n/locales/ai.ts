export const zhCN = {
  title: 'AI 网关',
  subtitle: '模型转发、OCR 识别与月度额度。',
  tabs: {
    back: '返回',
    tabsLabel: 'AI 网关分区',
    chat: 'ZVAgent',
    quota: '我的额度',
    keys: 'Key 管理',
    engines: 'OCR 引擎',
    models: '模型管理',
    'quota-admin': '额度管理',
    health: '健康状态',
    logs: '调用日志',
    monitor: '行为监控',
    scheduled: '定时任务'
  },
  common: {
    refresh: '刷新',
    create: '新建',
    edit: '编辑',
    delete: '删除',
    cancel: '取消',
    save: '保存',
    confirm: '确定',
    close: '关闭',
    copy: '复制',
    copied: '已复制',
    enabled: '启用',
    disabled: '停用',
    empty: '暂无数据',
    loadFailed: '加载失败',
    name: '名称',
    status: '状态',
    actions: '操作',
    createdAt: '创建时间',
    updatedAt: '更新时间',
    period: '计费周期',
    none: '—',
    yes: '是',
    no: '否'
  },
  chat: {
    model: '模型',
    modelLocked: '模型在建会话时确定',
    modelChangeable: '空会话可更换模型，发出第一条消息后锁定',
    modelLockedShort: '已锁定',
    modelChangeableShort: '可更换',
    thinking: '思考强度',
    thinkingSection: '思考过程',
    foldSettings: '收起设置',
    expandSettings: '展开设置',
    thinkingDefault: '默认',
    thinkingLow: '低',
    thinkingMedium: '中',
    thinkingHigh: '高',
    thinkingPlaceholder: '正在思考…',
    thinkingPlaceholderRounds: '正在思考…（已完成 {rounds} 轮）',
    sendingBlocked: '这一轮还在跑，先等它结束再发',
    safetyRejected: '该请求未通过内容安全审核。',
    modelsEmpty: '暂无可用模型，请联系管理员配置 Key。',
    modelsLoadFailed: '模型列表加载失败。',
    input: '输入内容',
    inputPlaceholder: '输入内容后发送',
    inputPlaceholderRunning: '这一轮还在跑，可以先写下一句',
    send: '发送',
    stop: '停止',
    requestId: '请求编号',
    copyRequestId: '复制请求编号',
    rateLimited: '请在 {seconds} 秒后重试',
    you: '我',
    assistant: 'ZVAgent',
    sessions: {
      title: '会话',
      subtitle: '会话按最近使用排序，回来可以接着聊。',
      list: '会话列表',
      openDrawer: '会话列表',
      new: '新建会话',
      loadMore: '加载更多',
      empty: '还没有会话',
      emptyHint: '新建一个会话开始对话。',
      pickFirst: '先新建一个会话再开始对话。',
      loadFailed: '会话列表加载失败',
      createFailed: '会话创建失败',
      untitled: '新会话',
      rename: '重命名',
      renameTitle: '重命名会话',
      renamePlaceholder: '输入会话标题',
      renamed: '会话已重命名',
      archive: '归档',
      unarchive: '取消归档',
      archived: '已归档',
      archivedNotice: '会话已归档',
      archivedEmpty: '还没有归档的会话',
      restore: '恢复',
      unarchivedNotice: '会话已恢复',
      remove: '删除会话',
      deleteConfirm: '确定删除该会话吗？会话内的消息与运行记录会一并删除，无法恢复。',
      deleteConfirmTitle: '删除会话',
      deletedNotice: '会话已删除',
      deleteFailed: '会话删除失败',
      switchTo: '切换到该会话',
      tokens: 'token {total}',
      toolRole: '工具',
      loadEarlier: '加载更早的消息',
      justNow: '刚刚',
      minutesAgo: '{count} 分钟前',
      hoursAgo: '{count} 小时前',
      daysAgo: '{count} 天前',
      context: {
        label: '上下文',
        usage: '{used} / {limit} token',
        percent: '占用 {percent}%',
        warning: '上下文已达 80%，会自动压缩，也可以手动压缩',
        autoCompactHint: '达到 80% 会自动压缩，也可手动压缩',
        autoCompactHintShort: '满 80% 自动压缩',
        compact: '压缩上下文',
        compacting: '压缩中…',
        compactOk: '上下文已压缩',
        compactOkTokens: '上下文已压缩（约 {before} → {after} token）',
        nothingToCompact: '暂无可压缩内容',
        compactBusy: '对话正在运行，请稍后压缩',
        compactFailed: '上下文压缩失败',
        compacted: '更早 {count} 条已压缩',
        degraded: '压缩降级：本次按截断处理'
      }
    }
  },
  agent: {
    title: 'ZVAgent',
    subtitle: '模型会调用平台工具查数据；写操作先生成提案，确认后才执行。',
    emptyHint: '输入问题后，模型会自己选择合适的工具查询。',
    running: '正在执行工具…',
    runningHint: '一次会话可能连续调用多轮工具，完成后统一返回结果。',
    events: '工具调用',
    eventCount: '共 {count} 次',
    result: '结果',
    kindWrite: '写操作',
    status: {
      executed: '已执行',
      proposed: '待确认',
      denied: '已拒绝',
      failed: '失败'
    },
    tools: {
      search_activities: '搜索活动',
      list_activities: '活动列表',
      get_activity: '活动详情',
      activity_statistics: '活动统计',
      list_activity_members: '活动成员',
      user_volunteer_time: '义工时长',
      user_activities: '参与活动',
      list_groups: '班级分组',
      update_member_duration: '修改成员时长',
      set_member_status: '审核报名状态',
      fs_list: '列出目录',
      fs_stat: '查看文件信息',
      fs_read: '读取文件',
      fs_write: '写入文件',
      fs_mkdir: '新建目录',
      fs_rename: '重命名文件',
      fs_delete: '删除文件',
      fs_chmod: '修改权限',
      db_schema: '查看数据表',
      db_columns: '查看表结构',
      db_rows: '读取数据行',
      db_query: '执行查询',
      db_insert: '插入数据行',
      db_update: '更新数据行',
      db_delete: '删除数据行',
      db_ddl: '执行建表语句',
      ssh_exec: '执行远程命令',
      platform_logs: '平台日志',
      db_diagnostics: '数据库诊断',
      backup_status: '备份状态',
      scheduler_status: '调度器状态',
      device_ban_list: '设备封禁',
      user_ban: '封禁用户',
      user_unban: '解封用户',
      device_unban: '设备解封',
      ai_call_logs: '调用日志',
      ai_gateway_status: '网关状态',
      ai_prompt_audit_list: '提示审计',
      ai_quota_set: '设置额度',
      ai_api_key_revoke: '吊销 Key',
      ban_history: '封禁历史',
      user_lookup: '查找用户',
      maintenance_mode: '维护模式',
      notify_send: '发布公告',
      agent_queue_status: '队列状态',
      server_time: '服务器时间',
      settings_snapshot: '设置快照',
      volunteer_audit: '义工核查',
      scheduler_jobs: '调度任务',
      scheduler_control: '调度控制',
      attachment_stats: '附件概览',
      attachment_gc_preview: '清理预览',
      attachment_gc_run: '执行清理',
      backup_run: '触发备份',
      script_catalog: '脚本目录',
      script_run: '运行脚本',
      platformHelp: '平台使用帮助',
      unknown: '未知工具'
    },
    preview: {
      none: '调用参数',
      diff: '文件差异',
      new_file: '新建文件',
      mkdir: '新建目录',
      rename: '重命名',
      delete: '删除文件',
      chmod: '修改权限',
      row_insert: '插入数据行',
      row_update: '更新数据行',
      row_delete: '删除数据行',
      sql: '数据库语句',
      command: '远程命令',
      args: '调用参数'
    },
    previewFields: {
      path: '目标路径',
      from: '原路径',
      to: '新路径',
      table: '数据表',
      key: '定位条件',
      values: '写入值',
      target: '执行目标',
      command: '命令',
      sql: '语句',
      summary: '摘要',
      verb: '语句类型',
      destructive: '破坏性语句',
      entries: '将删除的条目',
      entriesTruncated: '条目过多，列表已截断',
      truncated: '内容过长，已截断',
      mode: '权限',
      contentHead: '正文前 {shown} 行（共 {total} 行）',
      moreLines: '还有 {count} 行未展示'
    },
    dualAuth: {
      badge: '需双人动态码',
      cached: '30 分钟内已由管理员授权',
      scene: '执行「{tool}」，需另一位管理员提供动态码',
      cancelled: '已取消动态码授权',
      unavailable: '授权弹窗未就绪，请刷新页面后重试'
    },
    proposalErrors: {
      totpRequired: '该写操作需要动态码',
      totpRequiredHint: '请让另一位管理员提供 6 位动态码后重试。',
      forbidden: '动态码校验未通过',
      forbiddenHint: '动态码错误或已被使用，或当前没有第二位管理员可审批。',
      conflict: '提案已被处理',
      conflictHint: '这条提案可能已被确认或驳回，请刷新后查看最新状态。',
      expired: '提案已过期',
      expiredHint: '写操作提案十分钟内有效，请重新发起一次会话生成新提案。',
      generic: '确认失败',
      genericHint: '请稍后重试；若持续失败，请把请求编号交给管理员。'
    },
    stopReason: {
      none: '正常结束',
      noneHint: '本次运行正常结束。',
      completed: '正常完成',
      completedHint: '本次运行正常完成。',
      timeout: '执行超时',
      timeoutHint: '本次运行达到时间上限，已返回当前结果，可继续追问。',
      token_budget: 'token 预算用尽',
      token_budgetHint: '本次运行触及 token 上限，已返回当前结果，可继续追问。',
      max_iterations: '达到最大轮数',
      max_iterationsHint: '本次运行达到工具调用轮数上限，已返回当前结果，可继续追问。',
      cancelled: '已中止',
      cancelledHint: '本次运行被手动中止。',
      error: '执行出错',
      errorHint: '本次运行中途出错，已返回当前结果，可继续追问。'
    },
    pending: {
      title: '待确认提案',
      subtitle: '刷新页面后仍在这里等你处理。',
      count: '{count} 条待确认',
      empty: '没有待确认的提案',
      loadFailed: '待确认提案加载失败',
      refresh: '刷新',
      expand: '展开',
      collapse: '收起',
      entry: '查看待确认提案'
    },
    run: {
      rounds: '共 {rounds} 轮',
      usage: 'token {total}',
      replay: '回看这次运行',
      replayLoading: '正在回看…',
      replayFailed: '回看失败',
      replayEmpty: '这次运行没有留下工具记录',
      replayClose: '收起回看',
      background: '后台执行中',
      backgroundHint: '任务在服务端继续执行，关掉页面也不会中断。',
      queued: '排队中',
      running: '执行中',
      roundsDone: '已完成 {rounds} 轮',
      cancel: '中止运行',
      cancelling: '正在中止',
      cancelNotice: '已请求中止',
      cancelFailed: '中止失败',
      reconnectTitle: '接回进行中的任务',
      reconnectHint: '检测到未结束的任务，已恢复进度显示。',
      status: {
        queued: '排队中',
        running: '执行中',
        succeeded: '已完成',
        failed: '失败',
        cancelled: '已中止',
        interrupted: '已中断'
      }
    },
    proposal: {
      title: '待确认的写操作',
      pending: '待确认',
      confirming: '执行中',
      rejecting: '驳回中',
      confirmed: '已执行',
      rejected: '已驳回',
      expired: '已过期',
      error: '处理失败',
      confirm: '确认执行',
      reject: '驳回',
      validFor: '{minutes} 分钟内有效',
      validUnknown: '十分钟内有效',
      confirmedNotice: '写操作已执行',
      rejectedNotice: '提案已驳回',
      result: '执行结果',
      rawArgs: '查看原始参数'
    },
    unlock: {
      title: '输入密码以继续',
      prompt: '与 ZVAgent 交互需要验证你的账号密码，验证后 30 分钟内无需重复输入。',
      wrong: '密码不正确，请重试',
      locked: '尝试次数过多，请稍后再试',
      status: '已解锁，{min} 分钟内有效'
    },
    moderation: {
      rejected: '该请求已被内容审核拦截。多次尝试将按平台封禁规则处理。'
    },
    risk: {
      title: '使用前请阅读',
      power:
        'ZVAgent 拥有真实的写权限：可以封禁账号、删除数据、改动平台配置。它不会替你复核后果，一次误用就可能造成不可逆的损失。',
      ban: '平台会记录你与 ZVAgent 的全部交互。若被判定为越狱、提示词注入或滥用工具，将立即记录违规并按封禁规则处罚：警告并直接封号。',
      audit:
        '所有工具调用都会留痕审计：谁、何时、调用了哪个工具、传了什么参数、结果如何，全部可回溯，事后无法删除。',
      cancel: '取消',
      ack: '我已知悉'
    }
  },
  quota: {
    title: '我的额度',
    subtitle: '额度按自然月统计，周期结束后自动重置。',
    afTitle: 'AF 额度',
    afUnit: '单位：AF',
    ocrTitle: 'OCR 识别',
    chatTitle: '对话',
    used: '已用',
    limit: '上限',
    ruleTitle: '计费口径',
    ruleUnified: '对话与 OCR 共用同一个 AF 额度池，用量不分开累计。',
    ruleOcr: 'OCR 每次入队扣 1 AF。',
    rulePersonal: '个人 Key 的对话不占用本页额度。',
    ruleShared: '共享与服务端 Key 的对话按 AF 计入本人额度。',
    refresh: '刷新额度',
    loadFailed: '额度加载失败，请稍后重试。',
    usage: {
      title: '本月用量',
      subtitle: '按当月调用记录汇总，口径与调用日志一致。',
      calls: '调用次数',
      tokens: 'Token 总量',
      units: 'AF 消耗',
      byProvider: '按渠道',
      byModel: '按模型',
      provider: '渠道',
      model: '模型',
      unlabeled: '未标注',
      empty: '暂无用量记录',
      loadFailed: '用量统计加载失败，请稍后重试。',
      retry: '重试'
    },
    request: {
      apply: '申请重置额度',
      applied: '已申请，等待审批',
      dialogTitle: '申请重置额度',
      kind: '消耗类型',
      kindHint: '只是标记这次是哪类消耗；额度计入统一的 AF 池，不会多出一个池子。',
      reason: '说明',
      reasonPlaceholder: '简要说明为什么需要重置（可留空）',
      requestedLimit: '期望额度',
      requestedLimitHint: '可留空；填写后只是给审批人的参考，最终由管理员决定。',
      submit: '提交申请',
      submitted: '申请已提交，等待审批',
      submitFailed: '申请提交失败，请稍后重试。',
      pendingTitle: '待审批的申请',
      loadFailed: '申请记录加载失败'
    }
  },
  keys: {
    title: 'Key 管理',
    subtitle: '凭据只显示末四位提示，明文密钥不会回显。',
    reauthReason: '管理 AI 凭据属于高危操作，请输入密码完成再认证。',
    create: '新建凭据',
    edit: '编辑凭据',
    scope: '类型',
    scopePersonal: '个人',
    scopeShared: '共享',
    publicChannel: '公开渠道',
    modelCount: '模型数',
    owned: '本人',
    name: '名称',
    namePlaceholder: '小写字母、数字、连字符或下划线',
    baseUrl: '服务地址',
    baseUrlPlaceholder: '例如 https://api.example.com',
    models: '模型',
    modelsPlaceholder: '多个模型用逗号分隔',
    modelContext: '上下文窗口',
    modelContextPlaceholder: '留空表示未知，如 128000',
    modelContextHint: '按模型逐个选填上下文窗口（token 数）；留空按未知处理，填了会升级为带上下文的模型对象。',
    apiKey: '密钥',
    apiKeyPlaceholder: '至少 8 位',
    apiKeyHint: '留空表示不修改，填写即轮换密钥。',
    apiPath: '接口路径',
    apiPathPlaceholder: '留空使用默认路径',
    headers: '附加请求头',
    headersHint: '请求头的值不会回显；填写后提交会整体覆盖现有请求头集合。',
    headerName: '头名',
    headerValue: '头值',
    addHeader: '添加请求头',
    removeHeader: '移除该头',
    keyHint: '密钥提示',
    expires: '到期',
    expiresLabel: '到期时间',
    expiresPlaceholder: '留空表示永不过期',
    neverExpires: '永久',
    expired: '已过期',
    expiresHint: '到期后该凭据移出生效目录，依赖它的模型立即不可用；编辑时清空即可恢复永久。',
    empty: '还没有凭据',
    loadFailed: '凭据列表加载失败',
    createdNotice: '凭据已创建',
    savedNotice: '凭据已更新',
    deletedNotice: '凭据已删除',
    noChanges: '没有需要保存的修改',
    deleteConfirm: '确定删除该凭据吗？删除后依赖它的模型会立刻不可用。',
    deleteConfirmTitle: '删除凭据',
    readOnlyHint: '他人的个人凭据不可修改。',
    validation: {
      nameRequired: '请填写名称',
      namePattern: '名称只能是小写字母、数字、连字符或下划线，长度 2 到 64',
      baseUrlRequired: '请填写服务地址',
      baseUrlPattern: '服务地址必须以 http 或 https 开头',
      apiKeyRequired: '请填写密钥',
      apiKeyLength: '密钥长度需在 8 到 512 位之间',
      modelsTooMany: '模型最多 20 个',
      modelTooLong: '单个模型名称不能超过 80 个字符',
      modelContextInvalid: '上下文窗口需为正整数',
      expiresInPast: '到期时间必须晚于当前时间',
      headerNameRequired: '请填写请求头名称'
    }
  },
  engines: {
    title: 'OCR 引擎',
    subtitle: '引擎配置全站生效，凭证加密保存且永不回显。',
    create: '新建引擎',
    edit: '编辑引擎',
    name: '名称',
    kind: '类型',
    kindTencent: '腾讯云',
    kindAliyun: '阿里云',
    kindBaidu: '百度',
    kindGroq: 'Groq',
    secret: '凭证',
    secretHint: '留空表示不修改，填写即轮换凭证。',
    secretId: 'SecretId',
    secretKey: 'SecretKey',
    region: '地域',
    accessKeyId: 'AccessKeyId',
    accessKeySecret: 'AccessKeySecret',
    apiKey: 'ApiKey',
    apiKeySecret: 'ApiKeySecret',
    priority: '优先级',
    priorityHint: '数字越小越优先，取值 0 到 999。',
    config: '附加配置',
    configPlaceholder: 'JSON 对象，可留空',
    hasSecret: '已录入',
    noSecretYet: '未录入',
    empty: '还没有引擎',
    loadFailed: '引擎列表加载失败',
    createdNotice: '引擎已创建',
    savedNotice: '引擎已更新',
    deletedNotice: '引擎已删除',
    deleteConfirm: '确定删除该引擎吗？删除后站点将不再用它识别。',
    deleteConfirmTitle: '删除引擎',
    kindLocked: '引擎类型不可修改，需要更换时请删除后重建。',
    validation: {
      nameRequired: '请填写名称',
      namePattern: '名称只能是小写字母、数字、连字符或下划线，长度 2 到 40',
      priorityRange: '优先级需在 0 到 999 之间',
      secretRequired: '请填写该类型要求的凭证字段',
      configInvalid: '附加配置必须是合法的 JSON 对象'
    }
  },
  models: {
    title: 'AI 模型管理',
    subtitle: '维护模型元数据与计费倍率；表格里的「目录」行删除后回落到渠道默认值。',
    searchPlaceholder: '搜索模型、显示名或渠道',
    searchHint: '输入即过滤，按模型、显示名与渠道匹配。',
    retry: '重试',
    empty: '暂无模型记录',
    loadFailed: '模型列表加载失败',
    saveOk: '模型已保存',
    saveFailed: '模型保存失败',
    deletedOk: '覆盖已删除',
    deleteFailed: '删除失败',
    noChanges: '没有需要保存的改动',
    invalidRate: '倍率需为大于 0 的数字，如 0.5、1.25',
    invalidContext: '上下文窗口需为正整数，或留空',
    contextHint: '留空表示未知，按渠道目录的默认值处理。',
    rateHint: '倍率乘在每次调用的 AF 消耗上，支持小数（如 0.5、1.25）。',
    notesPlaceholder: '可选；写给其他管理员看的说明',
    edit: '编辑',
    editTitle: '编辑模型',
    delete: '删覆盖',
    deleteTitle: '删除模型覆盖',
    confirmDelete: '确定删除该模型的覆盖吗？删除后会回落到渠道目录的默认值。',
    deleteHint: '手工登记的行没有可回落的目录默认值，不能删除',
    inCatalog: '目录',
    manual: '手工',
    discover: {
      open: '从上游获取',
      title: '从上游获取模型元数据',
      hint: '读取渠道上游的模型列表与上下文窗口，只做预览，不改动现有配置。',
      provider: '渠道',
      providerPlaceholder: '选择或填写渠道名',
      providerManualHint: '当前列表里没有渠道，请手动填写凭据中登记的渠道名。',
      providerRequired: '请先选择或填写渠道',
      fetch: '获取',
      retry: '重试',
      idle: '选择渠道后点「获取」查看上游模型',
      failed: '获取上游模型失败',
      empty: '上游没有返回任何模型',
      contextMissing: '未提供',
      yes: '是',
      no: '否',
      statusApplied: '已回写',
      statusPending: '未回写',
      apply: '回写上下文长度',
      applyHint: '只回写上游给出且与本地不同的上下文长度。',
      applyOk: '已回写 {count} 个模型的上下文长度',
      applyFailed: '回写上下文长度失败',
      reauthReason: '从上游获取模型会使用渠道密钥，请确认密码。',
      columns: {
        model: '模型',
        contextLength: '上游上下文长度',
        inCredential: '已在渠道内',
        status: '状态'
      }
    },
    columns: {
      model: '模型',
      providers: '渠道',
      displayName: '显示名',
      contextLength: '上下文窗口',
      rate: '倍率',
      enabled: '启用',
      catalog: '来源',
      notes: '备注'
    }
  },
  quotaAdmin: {
    title: '额度管理',
    subtitle: '表格查看每个人的本月额度，并处理额度重置申请。',
    pickUser: '选择用户',
    pickUserPlaceholder: '输入姓名或学号搜索',
    noUser: '尚未选择用户',
    afTitle: 'AF 额度',
    afHint: '对话与 OCR 共用同一个 AF 池，这里改的就是它。',
    ocrTitle: 'OCR 识别',
    chatTitle: '对话',
    setLimit: '设置上限',
    setLimitTitle: '设置额度上限',
    limitKind: '额度类型',
    limitValue: '上限值',
    limitHint: '上限只改天花板，不影响已用量。',
    monthlyNotice: '额度按自然月计算，修改只影响当月；换月后按角色默认重建。',
    sourceExplicit: '库内已固化',
    sourceRoleDefault: '按角色默认',
    sourceGlobalDefault: '全局默认',
    reset: '重置本月用量',
    resetConfirm: '确定把该用户的本月已用量清零吗？上限保持不变。',
    resetConfirmTitle: '重置用量',
    savedNotice: '额度已更新',
    resetNotice: '用量已重置',
    loadFailed: '额度加载失败',
    retry: '重试',
    empty: '暂无额度数据',
    currentUser: '当前用户',
    searchPlaceholder: '搜索姓名或学号',
    search: '搜索',
    noPermission: '当前账号没有额度管理权限',
    noPermissionHint: '只有管理员或拥有 quota.manage 权限的角色可以访问这一页。',
    saveFailed: '额度保存失败',
    resetFailed: '重置失败',
    quickLookup: '按 oid 精确操作',
    quickLookupTitle: '按用户 oid 查看额度',
    quickLookupOid: '用户 oid',
    quickLookupPlaceholder: '粘贴或输入用户 oid',
    quickLookupHint: '查询结果会置顶显示在表格首行。',
    quickLookupDone: '已载入该用户的额度',
    quickLookupFailed: '查询失败，请检查 oid 是否正确',
    usersLoadFailed: '用户配额列表加载失败',
    users: {
      title: '用户配额',
      hint: '按姓名或学号搜索；分页由服务端处理。',
      user: '用户',
      af: 'AF 已用 / 上限',
      source: '来源',
      afShort: 'AF',
      pendingBadge: '有待审批申请',
      changeAfLimit: '改 AF 上限',
      resetAf: '清零 AF 用量',
      empty: '没有匹配的用户'
    },
    requests: {
      title: '重置申请',
      hint: '学生与学生会提交的额度重置申请，批准即清零本月用量。',
      user: '申请人',
      kind: '消耗类型',
      reason: '说明',
      createdAt: '申请时间',
      requestedLimit: '期望额度',
      approve: '批准',
      reject: '拒绝',
      approveTitle: '批准重置申请',
      rejectTitle: '拒绝重置申请',
      approveMode: '处理方式',
      approveResetOnly: '仅清零用量',
      approveResetLimit: '清零并设新上限',
      newLimit: '新上限',
      newLimitHint: '批准后该能力本月上限改成这个值。',
      approveHint: '批准会立即清零该能力的本月已用量，无法撤销。',
      approveLimitRequired: '请填写大于 0 的新上限',
      rejectNote: '拒绝理由',
      rejectNotePlaceholder: '可选；填写后会一并告知申请人',
      empty: '没有待审批的申请',
      loadFailed: '申请列表加载失败',
      approvedNotice: '申请已批准',
      rejectedNotice: '申请已拒绝',
      decisionFailed: '审批失败，请稍后重试'
    }
  },
  select: {
    needMoreDigits: '学号至少输入 {n} 位',
    needMoreName: '姓名至少输入 2 个字',
    invalidChars: '只支持学号或中文姓名',
    searching: '搜索中…',
    searchFailed: '搜索失败，请重试',
    noMatch: '没有匹配的用户'
  },
  userQuota: {
    entry: '配额',
    title: '配额设置',
    subtitle: 'AI 额度按自然月计算，附件上限按天计算。',
    aiSection: 'AI 额度',
    monthlyNotice: '修改只影响当月；换月后按角色默认重建。',
    uploadSection: '附件每日上传',
    inheritHint: '留空表示继承全局设置；填写后按用户覆盖。',
    dailyMaxFiles: '每日文件数',
    dailyMaxFilesUnit: '个',
    dailyMaxBytes: '每日总容量',
    dailyMaxBytesUnit: 'MB',
    effective: '当前生效：{files} 个 / {bytes} MB',
    filesPlaceholder: '留空继承全局',
    bytesPlaceholder: '留空继承全局',
    savedAi: 'AI 额度已更新',
    savedUpload: '上传配额已更新',
    loadFailed: '配额加载失败，请稍后重试。',
    loadFailedHint: '该接口依赖附件配额服务；若尚未部署，保存会失败。'
  },
  health: {
    title: '健康状态',
    subtitle: '只读视图：不含任何密钥材料。',
    enabled: '网关状态',
    enabledOn: '已开启',
    enabledOff: '已关闭',
    concurrency: '并发上限',
    quotaDefaults: '默认额度',
    defaultAf: 'AF 额度',
    credentials: '凭据',
    credentialPersonal: '个人',
    credentialShared: '共享',
    credentialServer: '服务端',
    sharedEntries: '共享凭据',
    sharedEmpty: '还没有共享凭据',
    providers: '服务商',
    columnName: '名称',
    columnKind: '类型',
    columnBaseUrl: '地址',
    columnModels: '模型',
    columnConfigured: '已配置',
    columnEnabled: '启用',
    cooldown: '熔断冷却',
    cooldownEmpty: '当前没有处于冷却的服务商',
    models: '可用模型',
    modelsEmpty: '暂无可用模型',
    loadFailed: '健康状态加载失败'
  },
  logs: {
    title: '调用日志',
    subtitle: '只读视图：记录每次调用的状态、延迟、token 用量与计费单位，不含任何密钥或对话正文。',
    filters: '过滤条件',
    user: '用户',
    userAll: '全部用户',
    capability: '能力',
    capabilityAll: '全部能力',
    capabilityOcr: 'OCR 识别',
    capabilityChat: '对话',
    status: '状态',
    statusAll: '全部状态',
    statusSuccess: '成功',
    statusError: '错误',
    statusTimeout: '超时',
    statusRejected: '已拒绝',
    statusBusy: '繁忙',
    statusAdminBypass: '管理员旁路',
    keyScope: '密钥来源',
    keyScopeAll: '全部来源',
    keyScopePersonal: '个人',
    keyScopeShared: '共享',
    keyScopeServer: '服务端',
    purpose: '场景',
    purposeAll: '全部场景',
    purposePlaceholder: '如 chat / markdown-assistant',
    provider: '服务商',
    providerAll: '全部服务商',
    providerPlaceholder: '精确匹配服务商名',
    model: '模型',
    modelAll: '全部模型',
    modelPlaceholder: '精确匹配模型名',
    requestId: '请求编号',
    requestIdPlaceholder: '按 request_id 追一条链',
    timeRange: '时间范围',
    timeRangeTo: '至',
    timeRangeStart: '开始时间',
    timeRangeEnd: '结束时间',
    timeRangeHint: '留空表示不限时间；筛选条件改动后自动回到第一页。',
    resetFilters: '重置过滤',
    stats: {
      title: '用量概览',
      days: '统计窗口',
      daysUnit: '天',
      daysHint: '按自然日统计（含今天），上限 90 天（与流水保留期一致）。',
      totalCalls: '调用次数',
      totalErrors: '失败次数',
      totalTokens: '合计 token',
      avgLatency: '平均延迟',
      errorRate: '失败率',
      tokenEstimatedHint: 'token 为空的行是上游没报 usage 的调用，其计费单位是估算值。',
      refreshFailed: '用量概览加载失败',
      daily: '按日',
      byProvider: '按服务商',
      byModel: '按模型',
      byPurpose: '按场景',
      byUser: '按用户 Top 50',
      byStatus: '按状态',
      date: '日期',
      calls: '次数',
      errors: '失败',
      units: '计费单位',
      tokens: 'token',
      noData: '窗口内没有调用记录'
    },
    columns: {
      createdAt: '时间',
      user: '用户',
      adminBadge: '管理员',
      capability: '能力',
      endpoint: '接口',
      provider: '服务商',
      model: '模型',
      purpose: '场景',
      status: '状态',
      httpStatus: 'HTTP',
      errorCode: '错误码',
      latency: '延迟',
      firstToken: '首字',
      tokens: 'token（入/出/合计）',
      attempt: '尝试',
      units: '计费单位',
      keyScope: '密钥来源',
      engineKind: '引擎类型',
      requestId: '请求编号'
    },
    latencyUnit: '毫秒',
    tokenEstimated: '估算',
    copyRequestId: '复制请求编号',
    empty: '没有符合条件的调用记录',
    loadFailed: '调用日志加载失败'
  },
  prompts: {
    title: 'Prompt 审阅',
    subtitle:
      '按用户、场景、时间与关键字检索受控留存的 prompt 元数据：列表不含正文，点开单条才读取，且每次读取都会记一条访问审计。',
    user: '用户',
    userAll: '全部用户',
    userOid: '用户 oid',
    userOidPlaceholder: '输入用户 oid 精确定位',
    purpose: '场景',
    purposeAll: '全部场景',
    timeRange: '时间范围',
    timeRangeTo: '至',
    timeRangeStart: '开始时间',
    timeRangeEnd: '结束时间',
    keyword: '关键字',
    keywordPlaceholder: '匹配 Prompt 正文，区分大小写',
    filterHint:
      '条件改动后自动重查并回到第一页；自定义时间范围优先于「最近 N 天」，关键字按字面子串匹配（区分大小写）。',
    resetFilters: '重置过滤',
    days: '时间窗口',
    daysUnit: '天',
    daysDisabled: '已使用自定义时间范围，最近 N 天不生效',
    daysHint: '滚动窗口：从当前时刻往前 N 天，上限 90 天（与正文留存期一致）。',
    badFilter: '时间范围无效，请重新选择',
    query: '查询',
    columns: {
      createdAt: '时间',
      user: '用户',
      roles: '角色快照',
      purpose: '场景',
      chars: '字符数'
    },
    empty: '窗口内没有留存的 prompt',
    loadFailed: 'prompt 列表加载失败',
    retry: '重试',
    view: '查看',
    detailTitle: 'prompt 正文',
    accessNotice: '查看正文会记录一条访问日志',
    accessNoticeHint: '审计只记录操作人、时间与目标记录，不会复制正文。',
    body: '正文',
    copyBody: '复制正文',
    copyBlocked: '浏览器不允许自动复制，请手动选择正文',
    noBody: '该条记录没有正文',
    answer: '回复',
    noAnswer: '该条记录没有留存回复',
    toolCalls: '工具调用',
    noToolCalls: '该次调用没有工具调用',
    toolStatus: '状态',
    toolArgs: '参数',
    toolResult: '结果',
    refused: '已拒答',
    warned: '已警告',
    detailFailed: '正文加载失败，请重试',
    detail: {
      user: '用户',
      purpose: '场景',
      roles: '角色快照',
      createdAt: '时间',
      requestId: '请求编号'
    }
  },
  schedule: {
    title: '定时任务',
    subtitle: '到点自动执行提示词，每跑一次都消耗你自己的额度。',
    notice: '到期时执行管理员已审核通过的提示词',
    noticeDesc:
      '执行使用你自己的可用 Key，并计入你的本月额度；未通过审核的任务不会执行。',
    noticeExtra: '间隔最小 15 分钟；修改提示词会让任务重新进入审核。',
    quotaTitle: '任务配额',
    quotaUnlimited: '不限',
    quotaUsed: '已用 {used}/{limit}',
    create: '新建任务',
    loadMore: '加载更多',
    refresh: '刷新',
    retry: '重试',
    empty: '还没有定时任务',
    loadFailed: '任务列表加载失败',
    quotaExceeded: '已达任务配额上限，请先删除一条再新建。',
    formCreate: '新建定时任务',
    formEdit: '编辑定时任务',
    fields: {
      name: '名称',
      namePlaceholder: '一句话说明这条任务做什么',
      prompt: '提示词',
      promptPlaceholder: '写下到点要交给模型的内容',
      promptCount: '{count} / {max} 字',
      scheduleKind: '排程方式',
      interval: '间隔（分钟）',
      intervalHint: '最小 15 分钟；每跑一次都会消耗一次额度。',
      cron: 'CRON 表达式',
      cronPlaceholder: '0 9 * * *',
      cronHint: '5 段式 crontab，按上海时区解释，例如 0 9 * * * 表示每天上午 9 点。',
      model: '模型',
      modelPlaceholder: '留空使用平台默认模型',
      enabled: '启用'
    },
    kind: {
      interval: '固定间隔',
      cron: 'CRON 表达式'
    },
    promptChanged: '修改提示词将重新进入审核',
    validation: {
      nameRequired: '请填写名称',
      nameTooLong: '名称最多 128 字',
      promptRequired: '请填写提示词',
      promptTooLong: '提示词最多 20000 字',
      intervalInvalid: '间隔至少 15 分钟，且必须是整数',
      cronInvalid: '不是合法的 5 段式 crontab'
    },
    createdNotice: '任务已提交审核',
    savedNotice: '任务已保存',
    deletedNotice: '任务已删除',
    reviewNotice: '审核结论已保存',
    runNotice: '已提交执行',
    enabledOn: '任务已启用',
    enabledOff: '任务已停用',
    deleteConfirm: '确定删除任务「{name}」吗？删除后不再执行，已产生的运行记录会保留。',
    deleteConfirmTitle: '删除该定时任务？',
    runNow: '立即执行',
    runNowHint: '只有审核通过的任务可以立即执行',
    history: '执行历史',
    reviewAction: '审核',
    columns: {
      name: '名称',
      reviewStatus: '审核状态',
      lastStatus: '最近状态',
      plan: '排程',
      nextRun: '下次执行',
      runCount: '执行次数',
      enabled: '启用',
      actions: '操作',
      owner: '所有者'
    },
    review: {
      draft: '草稿',
      pending: '待审核',
      approved: '已通过',
      rejected: '已驳回'
    },
    last: {
      none: '尚未执行',
      queued: '已入队',
      skipped_review: '未审核跳过',
      skipped_busy: '忙碌跳过',
      skipped_no_key: '无可用凭据',
      skipped_quota: '额度不足',
      skipped_owner: '账号不可用',
      skipped_flagged: '内容拦截',
      failed: '执行失败'
    },
    lastHint: {
      queued: '已进入执行队列，正在等待执行。',
      skipped_review: '提示词尚未通过管理员审核，这一轮没有执行。',
      skipped_busy: '你还有别的运行在进行中，这一轮被跳过。',
      skipped_no_key: '你没有可用的 AI 凭据，任务无法执行。',
      skipped_quota: '本月额度已用尽，这一轮被跳过。',
      skipped_owner: '任务所有者不存在或已被封禁，这一轮被跳过。',
      skipped_flagged: '提示词被内容规则拦截，这一轮没有执行。',
      failed: '执行过程中出错，可在执行历史里看详情。'
    },
    plan: {
      interval: '每 {minutes} 分钟',
      cron: 'cron {expr}',
      none: '未排期'
    },
    admin: {
      title: '管理员视图',
      hint: '默认列出全部用户的任务；选人可只看某人，审核状态由服务端过滤。',
      owner: '按人查看',
      ownerPlaceholder: '搜索姓名或学号',
      reviewFilter: '审核状态',
      filterAll: '全部',
      filterPending: '待审核',
      filterApproved: '已通过',
      filterRejected: '已驳回',
      filterDraft: '草稿'
    },
    reviewDialog: {
      title: '审核任务',
      approve: '通过',
      reject: '驳回',
      note: '备注',
      notePlaceholder: '驳回时说明原因，便于对方修改',
      submit: '提交结论'
    },
    runs: {
      title: '执行历史',
      empty: '这条任务还没有执行记录',
      loadFailed: '执行历史加载失败',
      status: '状态',
      model: '模型',
      startedAt: '开始',
      finishedAt: '结束',
      error: '错误',
      statusQueued: '排队中',
      statusRunning: '执行中',
      statusSucceeded: '已完成',
      statusFailed: '失败',
      statusCancelled: '已中止',
      statusInterrupted: '已中断'
    }
  },
  ocr: {
    action: '识别',
    running: '识别中',
    done: '已识别',
    failed: '识别失败',
    pending: '待识别',
    unsupported: '该附件不支持识别',
    viewText: '查看文本',
    copyText: '复制文本',
    engine: '引擎',
    confidence: '置信度',
    retry: '重试',
    emptyText: '没有识别到文字',
    noPermission: '当前角色没有识别权限',
    dialogTitle: '识别结果',
    autoLabel: '上传后自动识别（OCR）',
    autoHint: '每张图片上传成功后自动提交识别，会消耗本月 OCR 额度。'
  },
  errors: {
    AI_FORBIDDEN: {
      title: '无权使用 AI 服务',
      hint: '当前角色不在网关白名单内，请联系管理员开通。'
    },
    AI_RATE_LIMITED: {
      title: '请求过于频繁',
      hint: '已达到每分钟调用上限，请稍后再试。'
    },
    AI_BUSY: {
      title: '服务繁忙',
      hint: '网关并发已满，请稍后重试。'
    },
    AI_QUOTA_EXCEEDED: {
      title: '额度已用尽',
      hint: '本月 OCR 额度已用完，可等待下月重置或联系管理员调整上限。'
    },
    INSUFFICIENT_QUOTA: {
      title: '本月额度已用尽',
      hint: '共享与服务端 Key 的对话占用本人额度，可等待下月重置或联系管理员调整上限。'
    },
    AI_DISABLED: {
      title: 'AI 服务已关闭',
      hint: '网关当前处于关闭状态，请联系管理员开启。'
    },
    AI_NO_PROVIDER: {
      title: '暂无可用服务商',
      hint: '没有可用于该模型的服务商，请联系管理员检查 Key 配置。'
    },
    MODEL_NOT_FOUND: {
      title: '模型不可用',
      hint: '所选模型不在可用列表中，请重新选择。'
    },
    AI_UPSTREAM_AUTH: {
      title: '上游鉴权失败',
      hint: '上游拒绝了网关的凭据，请管理员检查 Key 是否过期。'
    },
    AI_UPSTREAM_RATE_LIMIT: {
      title: '上游限流',
      hint: '上游服务限流，请稍后重试。'
    },
    AI_UPSTREAM_ERROR: {
      title: '上游服务异常',
      hint: '上游返回错误，请稍后重试。'
    },
    AI_UPSTREAM_REJECTED: {
      title: '请求被上游拒绝',
      hint: '上游拒绝了本次请求，请检查模型与参数。'
    },
    AI_TIMEOUT: {
      title: '响应超时',
      hint: '上游长时间没有返回，请稍后重试。'
    },
    AI_CREDENTIAL_UNREADABLE: {
      title: '凭据无法读取',
      hint: '服务端无法解密已保存的 Key，请管理员重新录入。'
    },
    AI_UNSUPPORTED_MEDIA: {
      title: '不支持的文件类型',
      hint: '仅支持 jpeg、png 与 webp 图片。'
    },
    AI_OCR_IMAGE_TOO_LARGE: {
      title: '图片体积过大',
      hint: '请压缩或裁剪到 20MB 以内后重新上传。'
    },
    AI_OCR_NO_ENGINE: {
      title: '没有可用识别引擎',
      hint: '站点尚未配置 OCR 引擎，请联系管理员。'
    },
    AI_OCR_SITE_QUOTA_EXHAUSTED: {
      title: '识别额度已用尽',
      hint: '站点的识别额度已用完，请联系管理员。'
    },
    AI_KEY_DUPLICATE: {
      title: '名称已存在',
      hint: '换一个名称后再试。'
    },
    AI_KEY_INVALID: {
      title: '提交内容不合法',
      hint: '请检查名称、地址、密钥长度、模型与请求头格式。'
    },
    AI_KEY_NOT_FOUND: {
      title: '凭据不存在',
      hint: '该凭据可能已被删除，请刷新列表。'
    },
    AI_KEY_FORBIDDEN: {
      title: '无权操作该凭据',
      hint: '个人 Key 只能由其所有者修改。'
    },
    AI_KEY_BAD_EXPIRY: {
      title: '到期时间不合法',
      hint: '到期时间必须是合法时间且晚于当前时间。'
    },
    AI_LOGS_BAD_FILTER: {
      title: '过滤条件不合法',
      hint: '请检查能力、状态与密钥来源的取值后重试。'
    },
    AI_OCR_ENGINE_DUPLICATE: {
      title: '引擎名称已存在',
      hint: '换一个名称后再试。'
    },
    AI_OCR_ENGINE_INVALID: {
      title: '引擎配置不合法',
      hint: '请检查名称、优先级与凭证字段。'
    },
    AI_OCR_ENGINE_BAD_KIND: {
      title: '引擎类型不支持',
      hint: '请选择受支持的引擎类型。'
    },
    AI_OCR_ENGINE_NOT_FOUND: {
      title: '引擎不存在',
      hint: '该引擎可能已被删除，请刷新列表。'
    },
    AI_QUOTA_BAD_KIND: {
      title: '额度类型不合法',
      hint: '只能选择 OCR 或对话。'
    },
    AI_QUOTA_BAD_LIMIT: {
      title: '额度上限不合法',
      hint: '上限必须是零或正整数。'
    },
    AI_QUOTA_REQUEST_BAD_KIND: {
      title: '申请的能力类型不对',
      hint: '只能对 LLM 对话或 OCR 识别申请额度重置。'
    },
    AI_QUOTA_REQUEST_BAD_LIMIT: {
      title: '期望额度不合法',
      hint: '期望额度必须是零或正整数，或留空表示只重置用量。'
    },
    AI_QUOTA_REQUEST_BAD_STATUS: {
      title: '筛选条件不合法',
      hint: '审批列表只支持按待审批 / 已通过 / 已拒绝筛选。'
    },
    AI_QUOTA_REQUEST_PENDING: {
      title: '已有待审批的申请',
      hint: '同一能力本月只需申请一次，请等待管理员处理。'
    },
    AI_QUOTA_REQUEST_NOT_FOUND: {
      title: '申请不存在',
      hint: '这条申请可能已被处理或删除，刷新列表后再试。'
    },
    AI_QUOTA_REQUEST_NOT_PENDING: {
      title: '申请已被处理',
      hint: '同一条申请只能批准或拒绝一次，请刷新列表。'
    },
    AI_INVALID_REQUEST: {
      title: '请求参数不合法',
      hint: '请检查填写内容后重试。'
    },
    AI_AGENT_BAD_ARGUMENTS: {
      title: '会话内容不合法',
      hint: '输入内容为空或角色不被支持，请重新输入后发送。'
    },
    AI_AGENT_PROPOSAL_NOT_FOUND: {
      title: '提案不存在',
      hint: '提案可能已被清理，请重新发起一次对话生成新的提案。'
    },
    AI_AGENT_PROPOSAL_FORBIDDEN: {
      title: '不能处理他人的提案',
      hint: '写操作提案只能由发起人确认，请让发起人操作。'
    },
    AI_AGENT_PROPOSAL_CONFLICT: {
      title: '提案已处理',
      hint: '这条提案已经被确认或驳回，刷新页面查看最新状态。'
    },
    AI_AGENT_PROPOSAL_EXPIRED: {
      title: '提案已过期',
      hint: '写操作提案十分钟内有效，请重新发起对话生成新的提案。'
    },
    AI_AGENT_PROPOSAL_DENIED: {
      title: '没有执行权限',
      hint: '当前角色不允许执行该写操作，请联系管理员。'
    },
    AI_AGENT_PROPOSAL_TAMPERED: {
      title: '提案参数校验失败',
      hint: '提案记录与确认内容不一致，请重新生成提案。'
    },
    AI_AGENT_PROPOSAL_UNKNOWN_TOOL: {
      title: '提案工具不可用',
      hint: '该工具已下线或改名，请重新发起对话。'
    },
    AI_AGENT_TOOL_FAILED: {
      title: '写操作执行失败',
      hint: '服务端执行时出错，请稍后重试或检查参数。'
    },
    AI_AGENT_TOTP_REQUIRED: {
      title: '该写操作需要动态码',
      hint: '请让另一位管理员提供 6 位动态码后重试。'
    },
    AI_AGENT_UNLOCK_REQUIRED: {
      title: '需要先验证密码',
      hint: '与 ZVAgent 交互前需要验证账号密码，验证后 30 分钟内无需重复输入。'
    },
    AI_AGENT_UNLOCK_FAILED: {
      title: '密码验证失败',
      hint: '密码不正确，请重新输入。'
    },
    AI_AGENT_UNLOCK_LOCKED: {
      title: '验证尝试过于频繁，请稍后再试',
      hint: '连续输错会临时锁定，请过几分钟再试。'
    },
    AI_AGENT_MODERATION_REJECTED: {
      title: '请求已被内容审核拦截',
      hint: '该请求已被内容审核拦截。多次尝试将按平台封禁规则处理。'
    },
    AI_CONVERSATION_NOT_FOUND: {
      title: '会话不存在',
      hint: '会话可能已被删除，请刷新列表后重试。'
    },
    AI_CONVERSATION_MODE_MISMATCH: {
      title: '会话类型不匹配',
      hint: '当前会话与所选模式不是同一种，请新建一个会话或切换到对应类型的会话。'
    },
    AI_RUN_CONFLICT: {
      title: '已有任务在执行',
      hint: '同一会话同时只能有一个任务，请等它结束，或先中止它。'
    },
    AI_RUN_NOT_FOUND: {
      title: '任务不存在',
      hint: '该任务可能已被清理，请刷新页面后重试。'
    },
    AI_RUN_NOT_CANCELLABLE: {
      title: '任务已结束',
      hint: '该任务已处于终态，无需再中止。'
    },
    AI_RUN_INTERRUPTED: {
      title: '任务被中断',
      hint: '服务重启或执行进程失去心跳，这次任务没有跑完；已完成的轮次与提案仍然保留，可直接重发。'
    },
    AI_CONTEXT_UNAVAILABLE: {
      title: '上下文暂不可用',
      hint: '压缩上下文失败，服务端已降级为截断处理，本次回答可能不完整。'
    },
    AI_SCHEDULE_FORBIDDEN: {
      title: '没有操作该任务的权限',
      hint: '定时任务只能由创建者或管理员操作。'
    },
    AI_SCHEDULE_NOT_FOUND: {
      title: '任务不存在',
      hint: '这条任务可能已被删除，请刷新列表。'
    },
    AI_SCHEDULE_BAD_OID: {
      title: '任务标识不合法',
      hint: '请重新选择要查看的用户。'
    },
    AI_SCHEDULE_BAD_STATUS: {
      title: '审核状态取值不合法',
      hint: '只能按待审核、已通过、已驳回或草稿筛选。'
    },
    AI_SCHEDULE_BAD_KIND: {
      title: '排程方式不支持',
      hint: '请选择固定间隔或 CRON 表达式。'
    },
    AI_SCHEDULE_BAD_DECISION: {
      title: '审核结论不合法',
      hint: '请选择通过或驳回。'
    },
    AI_SCHEDULE_BAD_CRON: {
      title: 'CRON 表达式不合法',
      hint: '请填写 5 段式 crontab，例如 0 9 * * *。'
    },
    AI_SCHEDULE_BAD_INTERVAL: {
      title: '执行间隔不合法',
      hint: '间隔至少 15 分钟，且必须是整数。'
    },
    AI_SCHEDULE_QUOTA_EXCEEDED: {
      title: '任务配额已用完',
      hint: '学生会角色最多保留 1 条定时任务，请先删除一条再新建。'
    },
    AI_SCHEDULE_NOT_APPROVED: {
      title: '任务尚未通过审核',
      hint: '只有审核通过的任务可以立即执行。'
    },
    AI_SCHEDULE_BUSY: {
      title: '已有任务在执行',
      hint: '你还有别的运行在进行，请等它结束后再试。'
    },
    AI_SCHEDULE_NO_OWNER: {
      title: '任务所有者不可用',
      hint: '所有者不存在或已被封禁，任务无法执行。'
    },
    AI_SCHEDULE_NO_PROVIDER: {
      title: '没有可用凭据',
      hint: '你没有可用的 AI Key，请先在 Key 管理里录入。'
    },
    AI_SCHEDULE_QUOTA_EXHAUSTED: {
      title: '本月额度已用尽',
      hint: '额度不足，任务无法执行。'
    },
    AI_SCHEDULE_MODERATION_REJECTED: {
      title: '提示词被内容规则拦截',
      hint: '请修改提示词后重新提交审核。'
    },
    AI_SCHEDULE_CONVERSATION_FAILED: {
      title: '任务会话创建失败',
      hint: '服务端没能为这条任务准备会话，请稍后重试。'
    },
    PERMISSION_DENIED: {
      title: '没有权限',
      hint: '当前账号不能访问该附件。'
    },
    ATTACHMENT_NOT_FOUND: {
      title: '附件不存在',
      hint: '附件可能已被删除，请刷新后重试。'
    },
    UNKNOWN: {
      title: '请求失败',
      hint: '请稍后重试；若持续失败，请把请求编号交给管理员。'
    }
  }
}

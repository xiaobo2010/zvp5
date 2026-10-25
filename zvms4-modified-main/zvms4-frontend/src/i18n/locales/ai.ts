export const zhCN = {
  title: 'AI 网关',
  subtitle: '模型转发、OCR 识别与月度额度。',
  tabs: {
    chat: '对话',
    quota: '我的额度',
    keys: 'Key 管理',
    engines: 'OCR 引擎',
    'quota-admin': '额度管理',
    health: '健康状态',
    logs: '调用日志',
    monitor: '行为监控'
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
    title: '对话',
    subtitle: '回答由上游服务商返回，平台只做转发与计费。',
    modes: {
      chat: '对话',
      agent: '智能体'
    },
    model: '模型',
    modelPlaceholder: '选择模型',
    modelsEmpty: '暂无可用模型，请联系管理员配置 Key。',
    modelsLoadFailed: '模型列表加载失败。',
    stream: '流式输出',
    streamOn: '逐字返回',
    streamOff: '一次性返回',
    clear: '清空会话',
    cleared: '会话已清空',
    input: '输入内容',
    inputPlaceholder: '输入内容后发送',
    send: '发送',
    stop: '停止',
    stopped: '已停止生成',
    interrupted: '响应中断',
    interruptedHint: '上游没有发出结束标记，回答可能不完整。',
    emptyTitle: '还没有对话',
    emptyHint: '选择模型后输入内容即可开始。',
    thinking: '正在生成',
    errorTitle: '请求失败',
    requestId: '请求编号',
    copyRequestId: '复制请求编号',
    quotaUsed: '已用 {used}，上限 {limit}',
    rateLimited: '请在 {seconds} 秒后重试',
    you: '我',
    assistant: '助手',
    sessions: {
      title: '会话',
      subtitle: '对话与智能体共用一套会话，回来可以接着聊。',
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
      remove: '删除会话',
      deleteConfirm: '确定删除该会话吗？会话内的消息与运行记录会一并删除，无法恢复。',
      deleteConfirmTitle: '删除会话',
      deletedNotice: '会话已删除',
      deleteFailed: '会话删除失败',
      switchTo: '切换到该会话',
      tokens: 'token {total}',
      modeChat: '对话',
      modeAgent: '智能体',
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
        warning: '上下文接近上限',
        compacted: '更早 {count} 条已压缩',
        degraded: '压缩降级：本次按截断处理'
      }
    }
  },
  agent: {
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
    }
  },
  quota: {
    title: '我的额度',
    subtitle: '额度按自然月统计，周期结束后自动重置。',
    ocrTitle: 'OCR 识别',
    ocrUnit: '单位：次',
    chatTitle: '对话',
    chatUnit: '单位：千 token',
    used: '已用',
    limit: '上限',
    ruleTitle: '计费口径',
    ruleOcr: 'OCR 每次入队扣 1 次额度。',
    rulePersonal: '个人 Key 的对话不占用本页额度。',
    ruleShared: '共享与服务端 Key 的对话占用本人额度。',
    refresh: '刷新额度',
    loadFailed: '额度加载失败，请稍后重试。'
  },
  keys: {
    title: 'Key 管理',
    subtitle: '凭据只显示末四位提示，明文密钥不会回显。',
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
    kindRapidocr: '本地引擎',
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
    noSecret: '本地引擎不需要凭证',
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
  quotaAdmin: {
    title: '额度管理',
    subtitle: '先选择用户，再查看与调整其本月额度。',
    pickUser: '选择用户',
    pickUserPlaceholder: '输入姓名或学号搜索',
    noUser: '尚未选择用户',
    ocrTitle: 'OCR 识别',
    chatTitle: '对话',
    setLimit: '设置上限',
    setLimitTitle: '设置额度上限',
    limitKind: '额度类型',
    limitValue: '上限值',
    limitHint: '上限只改天花板，不影响已用量。',
    reset: '重置本月用量',
    resetConfirm: '确定把该用户的本月已用量清零吗？上限保持不变。',
    resetConfirmTitle: '重置用量',
    savedNotice: '额度已更新',
    resetNotice: '用量已重置',
    loadFailed: '额度加载失败'
  },
  health: {
    title: '健康状态',
    subtitle: '只读视图：不含任何密钥材料。',
    enabled: '网关状态',
    enabledOn: '已开启',
    enabledOff: '已关闭',
    concurrency: '并发上限',
    localEngine: '本地引擎',
    localEngineReady: '已预热',
    localEngineCold: '未预热',
    quotaDefaults: '默认额度',
    defaultOcr: 'OCR',
    defaultChat: '对话',
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
    title: '行为监控',
    subtitle:
      '受控留存的 prompt 元数据：列表不含正文，点开单条才读取，且每次读取都会记一条访问审计。',
    userOid: '用户 oid',
    userOidPlaceholder: '输入用户 oid 精确定位',
    days: '时间窗口',
    daysUnit: '天',
    daysHint: '滚动窗口：从当前时刻往前 N 天，上限 90 天（与正文留存期一致）。',
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
    detailFailed: '正文加载失败，请重试',
    detail: {
      user: '用户',
      purpose: '场景',
      roles: '角色快照',
      createdAt: '时间',
      requestId: '请求编号'
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

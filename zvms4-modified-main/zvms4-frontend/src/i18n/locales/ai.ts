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
    logs: '调用日志'
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
    assistant: '助手'
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
    subtitle: '只读视图：记录每次调用的状态、延迟与计费单位，不含任何密钥材料。',
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
    timeRange: '时间范围',
    timeRangeTo: '至',
    timeRangeStart: '开始时间',
    timeRangeEnd: '结束时间',
    timeRangeHint: '留空表示不限时间；筛选条件改动后自动回到第一页。',
    resetFilters: '重置过滤',
    columns: {
      createdAt: '时间',
      user: '用户',
      capability: '能力',
      endpoint: '接口',
      provider: '服务商',
      model: '模型',
      status: '状态',
      httpStatus: 'HTTP',
      errorCode: '错误码',
      latency: '延迟',
      units: '计费单位',
      keyScope: '密钥来源',
      requestId: '请求编号'
    },
    latencyUnit: '毫秒',
    copyRequestId: '复制请求编号',
    empty: '没有符合条件的调用记录',
    loadFailed: '调用日志加载失败'
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
    dialogTitle: '识别结果'
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

"""``/api/ai`` 的端点域拆分包。

每个子模块导出自己的 ``router``（路径前缀为空——它们是同一个挂载点下的
不同 path），由 :mod:`routers.ai_router` 按原装饰器顺序聚合。共享的协议层
（错误体、审计落行、场景头）在 :mod:`routers.ai.common`。
"""

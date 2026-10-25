"""智能体执行核心：工具注册表、共享查询层、执行循环、写操作提案。

分层（依赖单向，无环）：

* ``tool_impl`` —— 纯数据层：所有查询/写入的实现，``AgentError`` 也定义在这里；
* ``tools`` —— 注册表：白名单、参数 schema 与收敛、权限谓词；
* ``proposal`` —— 写操作的 HITL：生成提案、确认执行、驳回、过期；
* ``executor`` —— 循环：每轮一次上游调用、逐轮计费与审计、把工具结果拼回对话。

HTTP 层在 ``routers/ai_agent_router.py``：只做协议翻译与门禁，不重复业务判断。
"""

from . import tools  # noqa: F401  (先注册活动域工具，ops_tools 依赖 TOOLS)
from . import ops_tools  # noqa: F401  (导入即自注册运维工具：7 读 + 10 写)

__all__ = ["ops_tools", "tools"]

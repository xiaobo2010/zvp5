"""角色策略（``services.roles``）。

目前只有一个模块 :mod:`services.roles.policy`：角色 -> 功能开关 / 月额度的
唯一读取入口。包化（而不是单文件）是为了给后续的角色权限工具留位置，并且让
``from services.roles import policy`` 这种导入在路由/服务层保持一致。
"""

from services.roles import policy

__all__ = ["policy"]

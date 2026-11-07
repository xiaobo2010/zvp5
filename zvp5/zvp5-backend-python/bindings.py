"""少数「运行时协作者」的活绑定（拆分后仍保留既有打桩点）。

这几个名字在拆分前都是 ``main`` 的模块级名字，测试在**应用构造之后**用
``monkeypatch.setattr(main, "<name>", ...)`` 替换它们：

* ``maintenance_enabled`` / ``maintenance_enabled_fresh`` —— 维护开关（``/api/``
  之外的所有请求都看它，打桩后要立刻生效）；
* ``oracle_ping`` —— ``/api/health`` 的探针；
* ``CERT_PATH`` / ``_cert_pem`` —— ``/api/cert`` 的公钥路径与内存缓存（复位
  缓存也是打桩 ``main._cert_pem = None``）；
* ``ZVMSLog`` —— 盾牌 418 路径写审计用的日志类。

函数搬到别的模块后，如果直接引用**本模块**的全局名就会错过替换点：打桩写的是
``main`` 上的名字，不会回写到这里。所以这些协作者统一走 :func:`get` /
:func:`set`：**调用时**才去 ``main`` 模块上取同名属性，取不到再用默认值
（``python main.py`` 时模块名是 ``__main__``，``main`` 不在 ``sys.modules`` 里，
此时退回本模块的 ``_fallback`` 字典）。

开销是一次 ``dict`` 查找，可以忽略。只给「测试确实在打桩」的协作者用，不要当
通用的延迟导入工具 —— 新代码要拿别的模块的东西就直接 import。
"""

from __future__ import annotations

import sys
from typing import Any

# 打桩点所在的宿主模块名（见模块 docstring 最后一段）。
HOST_MODULE = "main"

# 宿主不可用（``python main.py``）时的本地存储，语义与宿主上的属性一致。
_fallback: dict[str, Any] = {}


def _host():
    return sys.modules.get(HOST_MODULE)


def get(name: str, default: Any = None) -> Any:
    """取协作者：优先 ``main`` 上的同名属性，其次本地存储，最后 ``default``。"""
    host = _host()
    if host is not None and hasattr(host, name):
        return getattr(host, name)
    if name in _fallback:
        return _fallback[name]
    return default


def set_value(name: str, value: Any) -> None:
    """写协作者：有宿主就写宿主（打桩点看到的就是这份状态），否则写本地存储。"""
    host = _host()
    if host is not None:
        setattr(host, name, value)
    else:
        _fallback[name] = value

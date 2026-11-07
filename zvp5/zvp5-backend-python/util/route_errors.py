"""数据库类 router 的「不可用」错误边界（APIRoute 子类）。

历史背景：``db_system_router`` 与 ``db_metrics_router`` 各抄了一份几乎逐字相同
的 ``APIRoute`` 子类，只有日志前缀和 503 的 detail 文案不同。这里把行为收成一
处，差异降级为类属性，避免两份实现日后悄悄漂移。

行为（两条路由完全一致，且必须保持一致）：

* ``HTTPException`` / ``RequestValidationError`` 原样上抛 —— 那是端点自己
  决定的 4xx，不能被伪装成「数据库挂了」；
* 其余任何异常 → ``503`` + ``Cache-Control: no-store``，异常细节只进日志，
  绝不进响应体（stderr/DSN 之类不能外泄）。

``detail`` 与日志文案由子类覆盖：既有的响应体逐字节不变，是 HTTP 契约。
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

_logger = logging.getLogger(__name__)


class DatabaseAvailabilityRoute(APIRoute):
    """把非 HTTP 异常映射成 503 的基类；文案由子类按端点填。"""

    log_message = "Database request failed"
    unavailable_detail = "数据库暂时不可用"

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_availability_errors(request):
            # 鉴权与建连也访问 Oracle，发生在端点函数之前，必须一起兜住。
            try:
                return await handler(request)
            except (HTTPException, RequestValidationError):
                raise
            except Exception:
                _logger.exception(self.log_message)
                raise HTTPException(
                    status_code=503,
                    detail=self.unavailable_detail,
                    headers={"Cache-Control": "no-store"},
                )

        return with_availability_errors

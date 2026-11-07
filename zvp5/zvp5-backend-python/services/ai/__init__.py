"""AI 网关：配置视图、供应商调用、路由熔断、配额与调用审计。

分层：``routers/ai_router.py`` 只做协议翻译（zvp 约定体 / OpenAI 兼容体），
本包不碰 HTTP 异常体，只抛 :class:`services.ai.routing.RouteError` 并交给路由映射。
"""

"""主题注册表（前端配色变体的后端事实源）。

**新增主题 = `zvp5/themes/manifest.json` 加一条 + 这里加一条，不用动 DDL**：
``USERS.THEME`` 是 ``VARCHAR2(32)``，存的是主题 id 字符串而不是枚举/外键；
接口返回的列表、PUT 的合法性校验、管理员统计的口径全部从本模块的
:data:`THEMES` 派生。所以加一个主题不需要新迁移、不需要新路由、不需要改前端
API 层。

清单与注册表必须逐项一致（id 顺序、label、description、status）：
``tests/test_theme_manifest.py`` 守着这条契约——清单里有、这里没有，用户能打开
那个应用却保存不了偏好；这里多一个，前端会给出 404 的路径。

**为什么不把未知值当错误**：主题是显示偏好，不是权限。历史数据里可能出现
已下线的主题 id，读路径必须回落到 :data:`DEFAULT_THEME` 而不是 500；写路径
才做严格校验（未知主题 400，不落库）。

**顺序即展示顺序**：:data:`THEMES` 是 tuple，``GET /api/v1/themes`` 与统计接口
都按它输出，管理员看到的主题顺序与用户选择器一致。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """一个可选主题。``status`` 只用于展示（stable/experimental），不参与校验。"""

    id: str
    label: str
    description: str
    status: str
    #: ``True`` = only accounts carrying the raw ``student`` role may see or
    #: select it. Missing/unknown roles never qualify (fail closed).
    students_only: bool = False


THEMES: tuple[Theme, ...] = (
    Theme(
        id="canary",
        label="Canary",
        description="经典 Phi 设计：暗色棱角、青色判定线与现有导航",
        status="stable",
    ),
    Theme(
        id="art",
        label="Art",
        description="艺术展画：全屏画作随义工进度逐像素显影",
        status="experimental",
    ),
    Theme(
        id="canary-pro",
        label="Canary Pro",
        description="Pro 工作台：独立左侧导航、液态玻璃卡片与工作台布局",
        status="stable",
    ),
    Theme(
        id="astra-alpha",
        label="Astra-Alpha",
        description="3D 空间：星系工作台、立体卡片与沉浸式模块布局",
        status="experimental",
    ),
)

#: 默认主题：新账号的列默认值、未知值的回落目标都由它决定。
DEFAULT_THEME = "canary"

THEME_IDS: frozenset[str] = frozenset(theme.id for theme in THEMES)


def _clean(value: object) -> str:
    """主题 id 的唯一归一化口径：``strip`` + ``lower``。非字符串得到空串。"""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _audience_allows(theme: Theme, user: dict | None) -> bool:
    """Return whether ``user`` may see/select ``theme``.

    ``user=None`` means "registry inspection" (manifest parity tests and
    stats), not "anonymous caller"; callers that expose the catalog always
    pass the authenticated user. Student-only themes therefore fail closed
    for both missing roles and non-student roles.
    """
    if not theme.students_only:
        return True
    if user is None:
        return False
    roles = user.get("per") or []
    return "student" in roles


def normalize_theme_id(value: object, user: dict | None = None) -> str:
    """把库里的值/请求值归一化成注册表里的 id；未知、空值或无权访问回 :data:`DEFAULT_THEME`。

    读路径专用：历史值可能已被下线，显示上按默认主题处理，不抛错。
    """
    cleaned = _clean(value)
    for theme in THEMES:
        if theme.id == cleaned and _audience_allows(theme, user):
            return cleaned
    return DEFAULT_THEME


def is_valid_theme_id(value: object, user: dict | None = None) -> bool:
    """``value`` 归一化后是否命中注册表且对 ``user`` 可见（写路径的严格校验）。"""
    cleaned = _clean(value)
    for theme in THEMES:
        if theme.id == cleaned and _audience_allows(theme, user):
            return True
    return False


def theme_path(value: object) -> str:
    """主题 id → 该主题整站入口路径。

    **页面入口只由后端给出**：根主题是 ``/``，其余是 ``/themes/<id>/``。
    前端不再自己拼路径，也不再维护一份主题目录；清单/注册表增删主题时，
    切换器、登录后跳转和独立 Pro 站都读同一个字段。
    """
    cleaned = _clean(value)
    theme_id = cleaned if cleaned in THEME_IDS else DEFAULT_THEME
    return "/" if theme_id == DEFAULT_THEME else f"/themes/{theme_id}/"


def theme_items(user: dict | None = None) -> list[dict]:
    """``GET /api/v1/themes`` 的 ``items``：注册表顺序 + 对外字段。

    ``user=None`` 只用于内部/测试的全量视图；路由必须传入当前用户，否则
    学生专属主题会被误当成公开主题。
    """
    return [
        {
            "id": theme.id,
            "label": theme.label,
            "description": theme.description,
            "status": theme.status,
            # 后端直接返回页面入口，客户端只负责跳转，不自行推导路径。
            "path": theme_path(theme.id),
        }
        for theme in THEMES
        if user is None or _audience_allows(theme, user)
    ]

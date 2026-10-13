"""Mongo-free home for the V1 → V2 user-position mapping.

``trans_permissions`` is a pure mapping and does no database I/O; it used to
live in the legacy ``groups`` module, whose import pulled in the Mongo client
and dragged the database layer into the auth path. It lives here so
``util.object_id`` stays free of that dependency.
"""

from typings.user import UserPosition as UserPositionV1
from typings.user_v2 import UserPosition as UserPositionV2


def trans_permissions(permission: UserPositionV1) -> UserPositionV2:
    mapping = {
        UserPositionV1.student: "student",
        UserPositionV1.secretary: "monitor",
        UserPositionV1.department: "volunteer",
        UserPositionV1.admin: "admin",
        "volunteer": "volunteer",
        "monitor": "monitor",
        "admin": "admin",
        "student": "student",
        "club": "club",
    }
    return mapping.get(permission, "student")

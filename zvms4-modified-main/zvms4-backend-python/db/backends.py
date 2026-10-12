"""Repository factory for the data access layer (MongoDB backend).

The factory functions return a concrete repository honouring the matching
``db.repos.protocols`` interface. Implementations are imported lazily so that
importing this module never opens a connection.
"""

from __future__ import annotations

from typing import Any


def get_users_repo(session: Any = None) -> Any:
    """Return the ``UsersRepo`` (MongoDB adapter)."""
    from db.repos.mongo.users import MongoUsersRepo

    return MongoUsersRepo()


def get_groups_repo(session: Any = None) -> Any:
    """Return the ``GroupsRepo`` (MongoDB adapter; class side)."""
    from db.repos.mongo.groups import MongoGroupsRepo

    return MongoGroupsRepo()


def get_changelog_repo(session: Any = None) -> Any:
    """Return the ``ChangelogRepo`` (MongoDB adapter)."""
    from db.repos.mongo.changelog import MongoChangelogRepo

    return MongoChangelogRepo()


__all__ = [
    "get_users_repo",
    "get_groups_repo",
    "get_changelog_repo",
]

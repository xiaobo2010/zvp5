"""Per-module backend switch + repository factory for the Mongo → Oracle (ADB) migration.

Policy helper: :func:`oracle_enabled_for` answers "should module X use Oracle?"
by reading ``settings.ORACLE_ENABLED`` (master switch) and
``settings.ORACLE_MODULES`` (the migrated module list). It performs no DB I/O.

A module is only ever on **one** backend at a time — the switch flips reads and
writes together, so there is never dual-write drift.

The factory functions return a concrete repository honouring the matching
``db.repos.protocols`` interface. Implementations are imported lazily so that
importing this module never creates an engine, a session, or a connection.
"""

from __future__ import annotations

from typing import Any

import settings

# Modules the switch recognises. Kept in sync with settings.ORACLE_MODULES docs.
MODULES: frozenset[str] = frozenset(
    {
        "users",
        "groups",
        "activities",
        "activity_members",
        "time",
        "stats",
        "logs",
        "changelog",
    }
)


def oracle_enabled_for(module: str) -> bool:
    """Return whether ``module`` should be served by the Oracle backend.

    ``False`` when Oracle is globally disabled (``ORACLE_ENABLED`` is falsy) or
    when ``module`` is not listed in ``ORACLE_MODULES``. Unknown module names
    simply resolve to ``False``.
    """
    if not getattr(settings, "ORACLE_ENABLED", False):
        return False
    return module in getattr(settings, "ORACLE_MODULES", [])


def get_users_repo(session: Any = None) -> Any:
    """Return the ``UsersRepo`` for the active backend.

    ``session`` is only meaningful for the Oracle backend; the Mongo adapter
    ignores it and uses the process-wide motor handle.
    """
    if oracle_enabled_for("users"):
        from db.repos.oracle.users import OracleUsersRepo

        return OracleUsersRepo(session)
    from db.repos.mongo.users import MongoUsersRepo

    return MongoUsersRepo()


def get_groups_repo(session: Any = None) -> Any:
    """Return the ``GroupsRepo`` for the active backend (class side)."""
    if oracle_enabled_for("groups"):
        from db.repos.oracle.groups import OracleGroupsRepo

        return OracleGroupsRepo(session)
    from db.repos.mongo.groups import MongoGroupsRepo

    return MongoGroupsRepo()


def get_activities_repo(session: Any = None) -> Any:
    """Return the ``ActivitiesRepo`` for the active backend."""
    if oracle_enabled_for("activities"):
        from db.repos.oracle.activities import OracleActivitiesRepo

        return OracleActivitiesRepo(session)
    from db.repos.mongo.activities import MongoActivitiesRepo

    return MongoActivitiesRepo()


def get_members_repo(session: Any = None) -> Any:
    """Return the ``MembersRepo`` for the active backend."""
    if oracle_enabled_for("activity_members"):
        from db.repos.oracle.members import OracleMembersRepo

        return OracleMembersRepo(session)
    from db.repos.mongo.members import MongoMembersRepo

    return MongoMembersRepo()


def get_changelog_repo(session: Any = None) -> Any:
    """Return the ``ChangelogRepo`` for the active backend."""
    if oracle_enabled_for("changelog"):
        from db.repos.oracle.changelog import OracleChangelogRepo

        return OracleChangelogRepo(session)
    from db.repos.mongo.changelog import MongoChangelogRepo

    return MongoChangelogRepo()


def get_time_repo(session: Any = None) -> Any:
    """Return the ``TimeRepo`` for the active backend."""
    if oracle_enabled_for("time"):
        from db.repos.oracle.times import OracleTimeRepo

        return OracleTimeRepo(session)
    from db.repos.mongo.times import MongoTimeRepo

    return MongoTimeRepo()


def get_stats_repo(session: Any = None) -> Any:
    """Return the ``StatsCacheRepo`` for the active backend."""
    if oracle_enabled_for("stats"):
        from db.repos.oracle.stats import OracleStatsCacheRepo

        return OracleStatsCacheRepo(session)
    from db.repos.mongo.stats import MongoStatsCacheRepo

    return MongoStatsCacheRepo()


__all__ = [
    "MODULES",
    "oracle_enabled_for",
    "get_users_repo",
    "get_groups_repo",
    "get_activities_repo",
    "get_members_repo",
    "get_changelog_repo",
    "get_time_repo",
    "get_stats_repo",
]

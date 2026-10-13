"""Repository factory for the data access layer.

The application runs exclusively on Oracle Autonomous Database (ADB): every
factory here returns the Oracle implementation of the matching
:mod:`db.repos.protocols` interface.

Implementations are imported lazily so that importing this module never
creates an engine, a session, or a connection.
"""

from __future__ import annotations

from typing import Any


def get_users_repo(session: Any = None) -> Any:
    """Return the ``UsersRepo`` implementation."""
    from db.repos.oracle.users import OracleUsersRepo

    return OracleUsersRepo(session)


def get_groups_repo(session: Any = None) -> Any:
    """Return the ``GroupsRepo`` implementation (class side)."""
    from db.repos.oracle.groups import OracleGroupsRepo

    return OracleGroupsRepo(session)


def get_activities_repo(session: Any = None) -> Any:
    """Return the ``ActivitiesRepo`` implementation."""
    from db.repos.oracle.activities import OracleActivitiesRepo

    return OracleActivitiesRepo(session)


def get_members_repo(session: Any = None) -> Any:
    """Return the ``MembersRepo`` implementation."""
    from db.repos.oracle.members import OracleMembersRepo

    return OracleMembersRepo(session)


def get_changelog_repo(session: Any = None) -> Any:
    """Return the ``ChangelogRepo`` implementation."""
    from db.repos.oracle.changelog import OracleChangelogRepo

    return OracleChangelogRepo(session)


def get_time_repo(session: Any = None) -> Any:
    """Return the ``TimeRepo`` implementation."""
    from db.repos.oracle.times import OracleTimeRepo

    return OracleTimeRepo(session)


def get_stats_repo(session: Any = None) -> Any:
    """Return the ``StatsCacheRepo`` implementation."""
    from db.repos.oracle.stats import OracleStatsCacheRepo

    return OracleStatsCacheRepo(session)


def get_applogs_repo(session: Any = None) -> Any:
    """Return the ``AppLogsRepo`` implementation."""
    from db.repos.oracle.app_logs import OracleAppLogsRepo

    return OracleAppLogsRepo(session)


def get_tasks_repo(session: Any = None) -> Any:
    """Return the ``TasksRepo`` implementation."""
    from db.repos.oracle.tasks import OracleTasksRepo

    return OracleTasksRepo(session)


def get_imports_repo(session: Any = None) -> Any:
    """Return the ``ImportsRepo`` implementation."""
    from db.repos.oracle.imports import OracleImportsRepo

    return OracleImportsRepo(session)


def get_daily_exports_repo(session: Any = None) -> Any:
    """Return the ``DailyExportsRepo`` implementation."""
    from db.repos.oracle.daily_exports import OracleDailyExportsRepo

    return OracleDailyExportsRepo(session)


__all__ = [
    "get_users_repo",
    "get_groups_repo",
    "get_activities_repo",
    "get_members_repo",
    "get_changelog_repo",
    "get_time_repo",
    "get_stats_repo",
    "get_applogs_repo",
    "get_tasks_repo",
    "get_imports_repo",
    "get_daily_exports_repo",
]

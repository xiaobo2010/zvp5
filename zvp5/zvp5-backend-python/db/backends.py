"""Repository factory for the data access layer.

The application runs exclusively on Oracle Autonomous Database (ADB): every
factory here returns the Oracle implementation of the matching
:mod:`db.repos.protocols` interface.

The hot read paths (users / groups / activities / AI conversations) are wrapped
in the Redis read-through proxies from :mod:`db.cached_repos`. The wrapper is
transparent: it forwards unknown attributes, bypasses the cache entirely when a
caller-owned ``session`` is passed in, and degrades to the plain repository when
no ``REDIS_URI`` is configured. Tests that monkeypatch these factories keep
bypassing the cache on purpose.

Implementations are imported lazily so that importing this module never
creates an engine, a session, or a connection.
"""

from __future__ import annotations

from typing import Any


def get_users_repo(session: Any = None) -> Any:
    """Return the ``UsersRepo`` implementation (Redis read-through)."""
    from db.cached_repos import CachedUsersRepo
    from db.repos.oracle.users import OracleUsersRepo

    return CachedUsersRepo(OracleUsersRepo(session), use_cache=session is None)


def get_groups_repo(session: Any = None) -> Any:
    """Return the ``GroupsRepo`` implementation (class side, Redis read-through)."""
    from db.cached_repos import CachedGroupsRepo
    from db.repos.oracle.groups import OracleGroupsRepo

    return CachedGroupsRepo(OracleGroupsRepo(session), use_cache=session is None)


def get_activities_repo(session: Any = None) -> Any:
    """Return the ``ActivitiesRepo`` implementation (Redis read-through)."""
    from db.cached_repos import CachedActivitiesRepo
    from db.repos.oracle.activities import OracleActivitiesRepo

    return CachedActivitiesRepo(OracleActivitiesRepo(session), use_cache=session is None)


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


def get_files_repo(session: Any = None) -> Any:
    """Return the ``FilesRepo`` implementation."""
    from db.repos.oracle.files import OracleFilesRepo

    return OracleFilesRepo(session)


def get_bindings_repo(session: Any = None) -> Any:
    """Return the ``BindingsRepo`` implementation."""
    from db.repos.oracle.bindings import OracleBindingsRepo

    return OracleBindingsRepo(session)


def get_ban_rules_repo(session: Any = None) -> Any:
    """Return the ``BanRulesRepo`` implementation (规则封禁配置)."""
    from db.repos.oracle.ban_rules import OracleBanRulesRepo

    return OracleBanRulesRepo(session)


def get_ban_records_repo(session: Any = None) -> Any:
    """Return the ``BanRecordsRepo`` implementation (封禁流水)."""
    from db.repos.oracle.ban_records import OracleBanRecordsRepo

    return OracleBanRecordsRepo(session)


def get_notifications_repo(session: Any = None) -> Any:
    """Return the ``NotificationsRepo`` implementation (站内通知)."""
    from db.repos.oracle.notifications import OracleNotificationsRepo

    return OracleNotificationsRepo(session)


def get_device_bans_repo(session: Any = None) -> Any:
    """Return the ``DeviceBansRepo`` implementation (设备级封禁)."""
    from db.repos.oracle.device_bans import OracleDeviceBansRepo

    return OracleDeviceBansRepo(session)


def get_device_ban_counters_repo(session: Any = None) -> Any:
    """Return the ``DeviceBanCountersRepo`` implementation (设备处罚梯度)."""
    from db.repos.oracle.device_ban_counters import OracleDeviceBanCountersRepo

    return OracleDeviceBanCountersRepo(session)


def get_temp_passwords_repo(session: Any = None) -> Any:
    """Return the ``TempPasswordsRepo`` implementation (一次性临时密码)."""
    from db.repos.oracle.temp_passwords import OracleTempPasswordsRepo

    return OracleTempPasswordsRepo(session)


def get_device_challenges_repo(session: Any = None) -> Any:
    """Return the ``DeviceChallengesRepo`` implementation (异设备登录挑战)."""
    from db.repos.oracle.device_challenges import OracleDeviceChallengesRepo

    return OracleDeviceChallengesRepo(session)


def get_violations_repo(session: Any = None) -> Any:
    """Return the ``ViolationsRepo`` implementation (平台违规使用证据)."""
    from db.repos.oracle.violations import OracleViolationsRepo

    return OracleViolationsRepo(session)


def get_ai_call_logs_repo(session: Any = None) -> Any:
    """Return the ``AICallLogsRepo`` implementation (AI 网关调用流水)."""
    from db.repos.oracle.ai_call_logs import OracleAICallLogsRepo

    return OracleAICallLogsRepo(session)


def get_ai_quota_repo(session: Any = None) -> Any:
    """Return the ``AIQuotaRepo`` implementation (AI 额度)."""
    from db.repos.oracle.ai_quota import OracleAIQuotaRepo

    return OracleAIQuotaRepo(session)


def get_ai_credentials_repo(session: Any = None) -> Any:
    """Return the ``AICredentialsRepo`` implementation (AI provider 凭据)."""
    from db.repos.oracle.ai_credentials import OracleAICredentialsRepo

    return OracleAICredentialsRepo(session)


def get_ai_models_repo(session: Any = None) -> Any:
    """Return the ``AIModelsRepo`` implementation (AI 模型目录覆盖层, V050)."""
    from db.repos.oracle.ai_models import OracleAIModelsRepo

    return OracleAIModelsRepo(session)


def get_ai_ocr_engines_repo(session: Any = None) -> Any:
    """Return the ``AIOcrEnginesRepo`` implementation (OCR 引擎注册表)."""
    from db.repos.oracle.ai_ocr_engines import OracleAIOcrEnginesRepo

    return OracleAIOcrEnginesRepo(session)


def get_ai_api_keys_repo(session: Any = None) -> Any:
    """Return the ``AIApiKeysRepo`` implementation (OpenAI 面独立 API key)."""
    from db.repos.oracle.ai_api_keys import OracleAIApiKeysRepo

    return OracleAIApiKeysRepo(session)


def get_ai_scheduled_tasks_repo(session: Any = None) -> Any:
    """Return the ``AiScheduledTasksRepo`` implementation (AI 定时任务)."""
    from db.repos.oracle.ai_scheduled_tasks import OracleAiScheduledTasksRepo

    return OracleAiScheduledTasksRepo(session)


def get_ai_agent_events_repo(session: Any = None) -> Any:
    """Return the ``AIAgentEventsRepo`` implementation (agent 工具调用留痕)."""
    from db.repos.oracle.ai_agent_events import OracleAIAgentEventsRepo

    return OracleAIAgentEventsRepo(session)


def get_ai_agent_acks_repo(session: Any = None) -> Any:
    """Return the ``AIAgentAcksRepo`` implementation (Agent 警示已确认)."""
    from db.repos.oracle.ai_agent_acks import OracleAIAgentAcksRepo

    return OracleAIAgentAcksRepo(session)


def get_ai_prompt_audit_repo(session: Any = None) -> Any:
    """Return the ``AIPromptAuditRepo`` implementation (受控的 prompt 正文留存)."""
    from db.repos.oracle.ai_prompt_audit import OracleAIPromptAuditRepo

    return OracleAIPromptAuditRepo(session)


def get_ai_conversations_repo(session: Any = None) -> Any:
    """Return the ``AIConversationsRepo`` implementation (持久会话, Redis 读穿)."""
    from db.cached_repos import CachedAIConversationsRepo
    from db.repos.oracle.ai_conversations import OracleAIConversationsRepo

    return CachedAIConversationsRepo(
        OracleAIConversationsRepo(session), use_cache=session is None
    )


def get_ai_messages_repo(session: Any = None) -> Any:
    """Return the ``AIMessagesRepo`` implementation (会话内消息)."""
    from db.repos.oracle.ai_conversations import OracleAIMessagesRepo

    return OracleAIMessagesRepo(session)


def get_ai_runs_repo(session: Any = None) -> Any:
    """Return the ``AIRunsRepo`` implementation (后台执行的智能体 run)."""
    from db.repos.oracle.ai_runs import OracleAIRunsRepo

    return OracleAIRunsRepo(session)


def get_system_settings_repo(session: Any = None) -> Any:
    """Return the ``SystemSettingsRepo`` implementation (在线设置中心)."""
    from db.repos.oracle.system_settings import OracleSystemSettingsRepo

    return OracleSystemSettingsRepo(session)


def get_user_quota_overrides_repo(session: Any = None) -> Any:
    """Return the ``UserQuotaOverridesRepo`` implementation (按用户配额覆盖)."""
    from db.repos.oracle.system_settings import OracleUserQuotaOverridesRepo

    return OracleUserQuotaOverridesRepo(session)


def get_user_warnings_repo(session: Any = None) -> Any:
    """Return the ``UserWarningsRepo`` implementation (警告账本)."""
    from db.repos.oracle.user_warnings import OracleUserWarningsRepo

    return OracleUserWarningsRepo(session)


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
    "get_files_repo",
    "get_bindings_repo",
    "get_ban_rules_repo",
    "get_ban_records_repo",
    "get_notifications_repo",
    "get_device_bans_repo",
    "get_device_ban_counters_repo",
    "get_temp_passwords_repo",
    "get_device_challenges_repo",
    "get_violations_repo",
    "get_ai_call_logs_repo",
    "get_ai_quota_repo",
    "get_ai_credentials_repo",
    "get_ai_models_repo",
    "get_ai_ocr_engines_repo",
    "get_ai_api_keys_repo",
    "get_ai_agent_events_repo",
    "get_ai_agent_acks_repo",
    "get_ai_prompt_audit_repo",
    "get_ai_conversations_repo",
    "get_ai_messages_repo",
    "get_ai_runs_repo",
    "get_system_settings_repo",
    "get_user_quota_overrides_repo",
    "get_user_warnings_repo",
]

"""Repository protocols for the ADB data layer.

These are pure :class:`typing.Protocol` interfaces: no I/O, no implementations.
Every domain has exactly one protocol, satisfied by the Oracle implementation:

* the Oracle implementations (``db.repos.oracle.*``) — SQLAlchemy Core over ADB.

The protocols are split into one module per domain (``db.repos.protocols.<domain>``)
and re-exported here, so ``from db.repos.protocols import UsersRepo`` keeps
working unchanged — importing from a domain module directly is equivalent.

Row shape contract
------------------
Mongo rows are plain ``dict``s keyed by the original document field names
(``_id``, ``id``, ``class_id``, ``on-campus`` …). Oracle implementations must
return **the same dict shape** (``_id`` normalised to a lowercase hex string)
so callers in ``routers/`` and ``util/`` are drop-in compatible. Returning
ORM/Row objects is a contract violation — the adapters translate.

DB-specific values (``ObjectId``, ``datetime``, ``None`` for NULL) are likewise
the adapters' responsibility to normalise; this package only pins the *interfaces*.
"""

from __future__ import annotations

# 历史遗留的「附带」名字：单文件时代它们只是 typing/datetime 的导入，却同样
# 出现在 ``dir(db.repos.protocols)`` 里，可能被调用方顺手 import 过。拆分后
# 原样保留，不改变任何人的导入路径。
from typing import Any, Mapping, Protocol, Sequence  # noqa: F401

from datetime import datetime  # noqa: F401

from .activities import ActivitiesRepo, MembersRepo
from .ai_calls import AIAgentEventsRepo, AICallLogsRepo, AIPromptAuditRepo
from .ai_credentials import (
    AIApiKeysRepo,
    AICredentialsRepo,
    AIModelsRepo,
    AIOcrEnginesRepo,
    DuplicateApiKey,
    DuplicateOcrEngine,
)
from .ai_quota import AIQuotaRepo, AIQuotaRequestsRepo
from .ai_runs import (
    AIConversationsRepo,
    AIMessagesRepo,
    AIRunsRepo,
    ActiveRunConflict,
    AiScheduledTasksRepo,
)
from .auth import BindingsRepo, DeviceChallengesRepo, TempPasswordsRepo
from .common import SHARED_OWNER_OID, UNSET, Row, _UnsetType
from .exports import DailyExportsRepo, ImportsRepo, TasksRepo
from .files import FilesRepo
from .groups import GroupsRepo
from .logs import AppLogsRepo
from .moderation import (
    BanRecordsRepo,
    BanRulesRepo,
    DeviceBanCountersRepo,
    DeviceBansRepo,
    ViolationsRepo,
)
from .notifications import ChangelogRepo, NotificationsRepo
from .time_stats import StatsCacheRepo, TimeRepo
from .users import RolePoliciesRepo, UserWarningsRepo, UsersRepo

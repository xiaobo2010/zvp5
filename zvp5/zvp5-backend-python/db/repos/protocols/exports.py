"""Export/import queues and their caches (``tasks`` / ``imports`` /
``daily_exports``)."""

from __future__ import annotations

from typing import Any, Protocol

from datetime import datetime

from .common import Row


class TasksRepo(Protocol):
    """Data access for ``zvms_new.tasks`` (runtime export queue).

    Satisfied by both the Mongo adapter and the Oracle implementation. Rows use
    the Mongo document shape ``{"_id": <hex str>, "id": <uuid.UUID>,
    "status": str, "format": str, "variant": str, "export_start": datetime,
    "export_end": datetime, "task_start": datetime, "task_end": datetime,
    "percentage": float, "errmsg": str, "result": list, "allow_cache": bool,
    "include_description": bool}``. ``id`` is accepted/returned as a
    :class:`uuid.UUID`; the Oracle ``TASK_UUID`` column stores its canonical
    string form.
    """

    async def create(self, document: Row) -> str:
        """Insert a task from its Mongo-shaped ``document``; return the new ``_id``.

        Lifts the ``insert_one(document)`` writes in ``routers/exports_router.py``.
        """
        ...

    async def get_by_uuid(self, task_uuid: Any) -> Row | None:
        """Return the task with ``id == task_uuid`` (UUID), or ``None``.

        Lifts the ``find_one({"id": UUID(...)})`` lookups in
        ``routers/exports_router.py``.
        """
        ...

    async def get(self, oid: str) -> Row | None:
        """Return the task with the given ``_id`` (hex string), or ``None``."""
        ...

    async def list_all(self) -> list[Row]:
        """Return every task (``tasks.find({})``) as full rows.

        Lifts ``routers/exports_router.py.get_export_list``.
        """
        ...

    async def update_fields(self, task_uuid: Any, fields: Row) -> None:
        """Apply a partial ``$set`` of Mongo-keyed ``fields`` to the task.

        Lifts the ``$set`` half of the ``update_one`` calls in
        ``routers/exports_router.py.process_task``.
        """
        ...

    async def push_result(
        self, task_uuid: Any, rows: list[Row], fields: Row | None = None
    ) -> None:
        """Append ``rows`` to the task's ``result`` list (``$push $each``).

        Optionally also applies a ``$set`` of Mongo-keyed ``fields`` in the same
        update, mirroring the combined ``{"$set": ..., "$push": {"result":
        {"$each": ...}}}`` writes in ``routers/exports_router.py.process_task``.
        """
        ...

    async def delete(self, task_uuid: Any) -> None:
        """Delete the task with ``id == task_uuid`` (``delete_one``)."""
        ...

    async def fail_pending(self, reason: str) -> int:
        """Mark every non-``completed`` task ``failed``; return the row count.

        Lifts ``main.py::mark_all_tasks_failed``. ``task_end`` is stamped as well
        so the failed rows carry an age the retention purge can read.
        """
        ...

    async def count_expired_finished(self, cutoff: datetime) -> int:
        """Count the finished rows :meth:`purge_finished_batch` would delete.

        Read-only; shared by the retention preview and the purge loop.
        """
        ...

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` finished rows older than ``cutoff``.

        The retention predicate (design §2): ``status IN ('completed','failed')``
        and ``COALESCE(task_end, task_start)`` precedes ``cutoff``. ``0`` means
        nothing is left to purge; the caller loops (``tasks.retention``). One
        commit per call.
        """
        ...


class ImportsRepo(Protocol):
    """Data access for ``zvms_new.imports`` (import job records).

    The Oracle ``CONTENT`` column is a ``BLOB``; ``content`` is accepted as
    plain ``bytes``.
    """

    async def create(
        self,
        name: str,
        filename: str,
        description: str,
        user: str,
        date: str,
        content: Any,
    ) -> str:
        """Insert an import record and return its new ``_id`` as a hex string.

        Lifts ``routers/imports_router.py.upload_activity_excel``'s
        ``insert_one`` (``date`` is an ISO string, ``content`` the uploaded
        bytes)."""
        ...


class DailyExportsRepo(Protocol):
    """Data access for ``zvms_new.daily_exports`` (statement export cache).

    Satisfied by both the Mongo adapter and the Oracle implementation. Rows use
    the Mongo document shape ``{"_id": <hex str>, "created_at": datetime,
    "status": str, "data": list}`` (``data`` is a JSON array of spreadsheet
    rows).
    """

    async def delete_all(self) -> int:
        """Delete every row (``delete_many({})``); return the deleted row count.

        Lifts ``tasks/compute_time.py::generate_reports``.
        """
        ...

    async def insert(self, document: Row) -> str:
        """Insert one document and return its new ``_id`` as a hex string.

        ``document`` carries ``created_at`` / ``status`` / ``data``.
        """
        ...

    async def update_fields(self, oid: str, fields: Row) -> None:
        """Apply a partial ``$set`` of ``fields`` to the row."""
        ...

    async def push_data(self, oid: str, item: Row) -> None:
        """Append ``item`` to the row's ``data`` array (``$push``).

        Lifts ``tasks/compute_time.py``'s per-statement ``update_one``.
        """
        ...

    async def find_one(self, oid: str) -> Row | None:
        """Return the row with ``_id == oid`` (``find_one({"_id": ...})``), or ``None``."""
        ...

"""Oracle (ADB) repository implementations.

Concrete SQLAlchemy 2.0 Core implementations of the repository protocols in
:mod:`db.repos.protocols`, talking to Oracle Autonomous Database via the async
engine in :mod:`db.oracle`.

These Oracle classes are the only data-access implementation, selected by the
factories in :mod:`db.backends`. Every
method returns plain ``dict`` rows keyed by the **original document field names**
(``_id`` lowercase hex, ``id``, ``class_id``, ``roles`` …) so callers in
``routers/`` and ``util/`` stay storage-agnostic.

Importing this package performs **no** database I/O; connections only happen
when a repository method is awaited.
"""

from __future__ import annotations

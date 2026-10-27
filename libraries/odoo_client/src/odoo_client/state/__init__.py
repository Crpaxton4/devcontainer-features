"""Local state layer: event store and resolved SDK configuration.

Both :class:`LocalStateClient` (SQLite-backed event store) and
:class:`LocalConfig` (File > Env > Default settings) are peer dependencies
injected into commands alongside :class:`~odoo_client.client.client.OdooClient`.
"""

from .config import (
    LocalConfig,
    ModelIdsNotWritableError,
    OdooConnectionSettings,
)
from .db import (
    LocalStateClient,
    SCHEMA_DDL,
    assert_tracker_db_present,
    create_schema,
    current_repo_label,
    tracker_db_path,
)
from .models import (
    EventRecord,
    SessionWindow,
    TrackerStateMissingError,
    session_key,
)

__all__ = [
    "LocalStateClient",
    "LocalConfig",
    "ModelIdsNotWritableError",
    "OdooConnectionSettings",
    "EventRecord",
    "SessionWindow",
    "session_key",
    "SCHEMA_DDL",
    "assert_tracker_db_present",
    "create_schema",
    "current_repo_label",
    "tracker_db_path",
    "TrackerStateMissingError",
]

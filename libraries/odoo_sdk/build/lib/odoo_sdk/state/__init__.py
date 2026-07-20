"""Local state layer: session FSM store and resolved SDK configuration.

Both :class:`LocalStateClient` (SQLite-backed task-session FSM) and
:class:`LocalConfig` (File > Env > Default settings) are peer dependencies
injected into commands alongside :class:`~odoo_sdk.client.client.OdooClient`.
"""

from .config import LocalConfig, OdooConnectionSettings
from .db import (
    LocalStateClient,
    SCHEMA_DDL,
    create_schema,
    current_repo_label,
    tracker_db_path,
)
from .models import (
    EventRecord,
    InvalidStateTransitionError,
    SessionWindow,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
    TrackerStateMissingError,
    session_key,
)

__all__ = [
    "LocalStateClient",
    "LocalConfig",
    "OdooConnectionSettings",
    "TaskState",
    "TaskRun",
    "EventRecord",
    "SessionWindow",
    "session_key",
    "SCHEMA_DDL",
    "create_schema",
    "current_repo_label",
    "tracker_db_path",
    "TaskAlreadyRunningError",
    "TaskNotRunningError",
    "InvalidStateTransitionError",
    "TrackerStateMissingError",
]

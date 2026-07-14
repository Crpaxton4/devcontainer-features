"""Local state layer: session FSM store and resolved SDK configuration.

Both :class:`LocalStateClient` (SQLite-backed task-session FSM) and
:class:`LocalConfig` (File > Env > Default settings) are peer dependencies
injected into commands alongside :class:`~odoo_sdk.client.client.OdooClient`.
"""

from .config import LocalConfig, OdooConnectionSettings
from .db import LocalStateClient, TaskStateDB
from .models import (
    EventRecord,
    InvalidStateTransitionError,
    ProjectIdError,
    SessionWindow,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
    session_key,
)

__all__ = [
    "LocalStateClient",
    "TaskStateDB",
    "LocalConfig",
    "OdooConnectionSettings",
    "TaskState",
    "TaskRun",
    "EventRecord",
    "SessionWindow",
    "session_key",
    "TaskAlreadyRunningError",
    "TaskNotRunningError",
    "InvalidStateTransitionError",
    "ProjectIdError",
]

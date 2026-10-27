"""Shared in-memory fakes for the TUI tests (no live Odoo, no real terminal)."""

from __future__ import annotations

from typing import Any, Optional
from types import SimpleNamespace
from unittest.mock import MagicMock

from odoo_dev_data.config import LocalConfig


def sample_sessions() -> list[dict[str, Any]]:
    """Return a small set of session dicts in the ``query_sessions`` shape."""
    return [
        {
            "session_id": 1,
            "task_id": "101",
            "repo": "acme/web",
            "strategy_name": "development",
            "category": "Development",
            "started_at": "2026-06-01T09:00:00",
            "ended_at": "2026-06-01T11:00:00",
            "duration_secs": 7200,
            "events": [
                {
                    "event_id": 1,
                    "source": "commit",
                    "timestamp": "2026-06-01T09:30:00",
                    "task_ids": ["101"],
                    "repo": "acme/web",
                }
            ],
        },
        {
            "session_id": 2,
            "task_id": "202",
            "repo": "acme/api",
            "strategy_name": "development",
            "category": "Development",
            "started_at": "2026-06-01T10:00:00",
            "ended_at": "2026-06-01T12:30:00",
            "duration_secs": 9000,
            "events": [],
        },
    ]


class FakeCommand:
    """A stand-in registry command that records calls and returns a canned value."""

    def __init__(self, result: Any = None, state: Any = None):
        self._result = result
        self.state = state
        self.calls: list[dict[str, Any]] = []

    def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._result


class FakeService:
    """A stand-in command service: records each command's calls.

    ``execute`` dispatches to the pre-seeded :class:`FakeCommand` of that id;
    ``services`` carries the injected peers a ``TuiDeps`` reads.
    """

    def __init__(self, commands, client=None, store=None, config=None):
        self.commands = commands
        self.services = SimpleNamespace(
            client=client, events=store, config=config, jobs=None
        )

    def execute(self, command_id, /, **kwargs):
        return self.commands[command_id].execute(**kwargs)


def build_fake_store() -> MagicMock:
    """Return a MagicMock store safe for the driver's incidental reads.

    The empty-hint path counts events, triage reads unattributed
    events, and the shared upload loop's orphan sweep reads the ledger; canned
    empty results keep all of them harmless no-ops.
    """
    store = MagicMock()
    store.count_events.return_value = 0
    store.get_unattributed_events.return_value = []
    store.get_events_by_ids.return_value = []
    store.list_session_uploads.return_value = []
    store.get_events.return_value = []
    return store


def build_fake_deps(
    query_result: Optional[list[dict[str, Any]]] = None,
    *,
    commands: Optional[dict[str, FakeCommand]] = None,
    store: Any = None,
    client: Any = None,
    config: Any = None,
) -> Any:
    """Return a :class:`~odoo_tui.app.TuiDeps` over fakes (no Odoo, no SQLite).

    The driver receives its dependencies as an injected bundle; by default every
    peer is an in-memory stand-in: a service whose ``query_sessions`` returns
    ``query_result`` (the sample sessions when omitted), a MagicMock store with
    empty canned reads, a MagicMock client, and a config carrying only the
    session gap the empty hint renders.
    """
    from odoo_tui.app import TuiDeps

    if commands is None:
        commands = {
            "query_sessions": FakeCommand(
                result=sample_sessions() if query_result is None else query_result
            ),
        }
    return TuiDeps(
        service=FakeService(
            commands,
            client=client if client is not None else MagicMock(),
            store=store if store is not None else build_fake_store(),
            config=config if config is not None else LocalConfig(),
        )
    )

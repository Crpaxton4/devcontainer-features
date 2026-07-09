"""Shared in-memory fakes for the TUI tests (no live Odoo, no real terminal)."""

from __future__ import annotations

from typing import Any


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


class FakeRegistry:
    """A dict-backed registry returning pre-seeded :class:`FakeCommand`s."""

    def __init__(self, commands: dict[str, FakeCommand]):
        self._commands = commands

    def __getitem__(self, name: str) -> FakeCommand:
        return self._commands[name]


def build_fake_registry() -> FakeRegistry:
    """Return a registry whose commands never touch Odoo or SQLite state."""
    return FakeRegistry(
        {
            "query_sessions": FakeCommand(result=sample_sessions()),
            "start_task": FakeCommand(result={"session_id": 1}),
            "stop_task": FakeCommand(result={"elapsed_hours": 1.0}),
        }
    )

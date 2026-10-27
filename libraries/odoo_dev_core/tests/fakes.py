"""In-memory implementations of every core port, and ``fake_services``.

Each fake satisfies one :mod:`odoo_dev_core.ports` Protocol structurally. The
gateway fakes record their calls and return a canned summary. ``fake_services``
builds a full :class:`~odoo_dev_core.ports.Services` bundle: a real
``EventStore`` on a temp home for ``events`` and a ``LocalConfig`` for
``config``, unless the caller overrides them.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from odoo_dev_core import CommandService, Services, default_registry
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.repo_map import RepoMap, Resolved
from odoo_dev_data.tasks import TaskRecord

from .support import make_event_store
from odoo_dev_core import default_registry


class FakeRecordset:
    """Minimal stand-in for the recordset returned by ``client[model]``."""

    def __init__(self, rows):
        self._rows = rows
        self.searched_domain = None

    def search(self, domain):
        self.searched_domain = domain
        return self

    def read(self, fields):
        return [{key: row[key] for key in fields} for row in self._rows]


class FakeRpcClient:
    """A structural ``RpcClient`` with no dependency on ``OdooClient``."""

    def __init__(self, uid=1, models=None):
        self._uid = uid
        self._recordsets = {
            name: FakeRecordset(rows) for name, rows in (models or {}).items()
        }
        self.execute_calls = []

    @property
    def uid(self):
        return self._uid

    def execute(self, model, method, *args, **kwargs):
        self.execute_calls.append((model, method, args, kwargs))
        return {"model": model, "method": method}

    def __getitem__(self, model_name):
        return self._recordsets[model_name]


class _FakeGateway:
    """Record the keyword arguments of each sync call; return a canned summary."""

    def __init__(self, summary: Optional[dict[str, Any]] = None) -> None:
        self.summary = {"inserted": 0} if summary is None else summary
        self.calls: list[dict[str, Any]] = []

    def _record(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.summary


class FakeGit(_FakeGateway):
    def sync_git_log(self, state, config=None, client=None, **kwargs):
        return self._record(state, config, client, **kwargs)


class FakeGitHub(_FakeGateway):
    def sync_github(self, state, config=None, client=None, **kwargs):
        return self._record(state, config, client, **kwargs)


class FakeChatter(_FakeGateway):
    def sync_odoo_chatter(self, client, state, config=None, **kwargs):
        return self._record(client, state, config, **kwargs)


class FakeJobs:
    """A job ledger that counts ``increment_questions`` calls per job id."""

    def __init__(self) -> None:
        self.questions: dict[str, int] = {}

    def increment_questions(self, job_id: str) -> int:
        self.questions[job_id] = self.questions.get(job_id, 0) + 1
        return self.questions[job_id]

    def list_finished(self, *, start_us=None, end_us=None) -> list:
        return []


class FakeRepoMap:
    """An in-memory repo map."""

    def __init__(self, repo_map: Optional[RepoMap] = None) -> None:
        self.repo_map = repo_map or RepoMap()
        self.saved: list[RepoMap] = []

    def load(self) -> RepoMap:
        return self.repo_map

    def save(self, repo_map: RepoMap) -> Path:
        self.repo_map = repo_map
        self.saved.append(repo_map)
        return Path("repo-map.json")

    def resolve(
        self,
        query: Optional[str] = None,
        *,
        cwd: Optional[Path] = None,
        repos_dir: Optional[Path] = None,
    ) -> Resolved:
        return Resolved(project=None, repo_root=None, origin=None, warnings=())


class FakeTasks:
    """In-memory ``task.json`` records."""

    def __init__(self) -> None:
        self.records: dict[str, TaskRecord] = {}

    def read(self, task_id) -> TaskRecord:
        return self.records.get(str(task_id), TaskRecord(task_id=str(task_id)))

    def write(self, record: TaskRecord) -> Path:
        self.records[record.task_id] = record
        return Path(f"{record.task_id}/task.json")

    def append_friction(self, task_id, entries, *, job_id: str = "") -> int:
        record = self.read(task_id)
        record.friction.extend({**entry, "job_id": job_id} for entry in entries)
        self.records[record.task_id] = record
        return len(record.friction)


class FixedClock:
    """A clock that always reads the same aware UTC instant."""

    def __init__(self, now: Optional[datetime] = None) -> None:
        self._now = now or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now


def fake_services(**overrides: Any) -> Services:
    """Return a :class:`Services` bundle of fakes; ``overrides`` replace members.

    ``events`` defaults to a real ``EventStore`` on a fresh temp home and
    ``config`` to an empty ``LocalConfig``.
    """
    members: dict[str, Any] = {
        "client": FakeRpcClient(),
        "events": make_event_store(),
        "config": LocalConfig(),
        "git": FakeGit(),
        "github": FakeGitHub(),
        "chatter": FakeChatter(),
        "jobs": None,
        "repo_map": FakeRepoMap(),
        "tasks": FakeTasks(),
        "clock": FixedClock(),
        "env": {},
    }
    unknown = set(overrides) - set(members)
    if unknown:
        raise TypeError(f"unknown Services member(s): {sorted(unknown)}")
    members.update(overrides)
    return Services(**members)


def service(**overrides: Any) -> CommandService:
    """Return a :class:`CommandService` over the builtins and ``fake_services``."""
    return CommandService(default_registry(), fake_services(**overrides))

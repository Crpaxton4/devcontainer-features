"""Import every legacy store into the ``ODOO_DEV_HOME`` layout.

``python -m odoo_dev_data doctor --migrate [--dry-run]``. The import is
idempotent and never deletes or modifies a legacy store. Each step appends one
:class:`Step`. A step is ``done`` when it changed the new home, ``skipped``
when there was nothing to do and ``refused`` when it found a state it must not
import. A dry run touches nothing: a step that would act reports ``skipped``
with a detail that starts ``would``.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import LocalConfig
from .db.engine import connect, transaction
from .home import (
    Paths,
    ensure_home,
    ensure_job_bundle_dir,
    ensure_task_dir,
    paths,
    resolve_home,
)
from .jobs import JobStore
from .repo_map import from_json, save_repo_map, validate
from .tasks import brief_path

DONE, SKIPPED, REFUSED = "done", "skipped", "refused"

LEGACY_TRACKER_VERSION = 4
LEGACY_BATCH = "legacy-task-runs"
LEGACY_REPO = "legacy"
MARK_TRACKER = "migrated.tracker"
MARK_JOBS = "migrated.jobs"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_DRAFT = re.compile(r"^draft-[0-9]+\.md$")
_LEGACY_MAP_KEYS = frozenset(
    {"_doc", "repo_path", "default_branch", "flow_confirmed", "remote", "notes"}
)
_OPEN_RUN_STATES = ("RUNNING", "AWAITING_ANSWERS")

_TRACKER_TABLES = {
    "events": "id, source, timestamp, task_ids, repo, pr_num, branch, subject, "
    "payload, external_id",
    "session_uploads": "session_key, timesheet_id, hours, uploaded_at, task_id, "
    "started_at, ended_at",
    "chatter_dedupe": "task_id, dedupe_key, message_id, created_at",
    "settings": "key, value",
}
_LEGACY_JOB_COLUMNS = (
    "job_id",
    "batch_id",
    "task_id",
    "repo",
    "round",
    "state",
    "brief",
    "submitted_us",
    "started_us",
    "ended_us",
    "session_id",
    "container",
    "worktree",
    "outcome",
    "dead_reason",
    "detail",
    "entry",
)
_JOB_DEFAULTS: dict[str, Any] = {
    "branch": "",
    "pr_num": 0,
    "tool_version": "",
    "envelope": "",
    "transcript_path": "",
    "bundle_dir": "",
    "has_task_json": 0,
    "has_session": 0,
    "has_envelope": 0,
    "bytes": 0,
    "harvested_us": None,
    "missing": "",
}


@dataclass(frozen=True)
class Step:
    name: str
    action: str  # "done" | "skipped" | "refused"
    detail: str


@dataclass(frozen=True)
class MigrateReport:
    home: Path
    dry_run: bool
    steps: tuple[Step, ...]

    @property
    def ok(self) -> bool:
        return all(step.action != REFUSED for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "dry_run": self.dry_run,
            "steps": [
                {"name": s.name, "action": s.action, "detail": s.detail}
                for s in self.steps
            ],
            "ok": self.ok,
        }


class _Ctx:
    """What every step shares: the target home, the environment and one connection."""

    def __init__(
        self,
        home: Path,
        env: Mapping[str, str],
        dry_run: bool,
        now_us: int | None,
    ) -> None:
        self.home = home
        self.layout: Paths = paths(home)
        self.env = env
        self.dry_run = dry_run
        self.now_us = now_us
        self._conn: sqlite3.Connection | None = None

    def expand(self, text: str) -> Path:
        if text == "~" or text.startswith("~/"):
            base = self.env.get("HOME") or str(Path.home())
            return Path(base + text[1:])
        return Path(text)

    def env_dir(self, name: str) -> Path | None:
        value = self.env.get(name)
        return self.expand(value) if value else None

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect(self.home, now_us=self.now_us)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def marker(self, key: str) -> bool:
        if self.dry_run:
            return _peek_marker(self.layout.state_db, key)
        row = self.conn().execute("SELECT 1 FROM settings WHERE key=?", (key,))
        return row.fetchone() is not None

    def would(self, name: str, detail: str) -> Step:
        return Step(name, SKIPPED, f"would {detail}")


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _peek_marker(state_db: Path, key: str) -> bool:
    if not state_db.is_file():
        return False
    try:
        with closing(_ro(state_db)) as conn:
            row = conn.execute("SELECT 1 FROM settings WHERE key=?", (key,))
            return row.fetchone() is not None
    except sqlite3.Error:
        return False


def _first_file(dirs: Iterable[Path | None], name: str) -> Path | None:
    for directory in dirs:
        if directory is not None and (directory / name).is_file():
            return directory / name
    return None


def _us(text: str) -> int:
    """Parse a legacy ISO timestamp (UTC when naive) into microseconds."""
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((parsed - _EPOCH) // timedelta(microseconds=1), 1)


# --- step 1: home ----------------------------------------------------------


def _step_home(ctx: _Ctx) -> Step:
    layout = ctx.layout
    complete = layout.tasks.is_dir() and layout.locks.is_dir()
    if complete:
        return Step("home", SKIPPED, str(ctx.home))
    if ctx.dry_run:
        return ctx.would("home", "create")
    ensure_home(ctx.home)
    return Step("home", DONE, str(ctx.home))


# --- step 2: config --------------------------------------------------------


def _config_source(ctx: _Ctx) -> Path | None:
    configured = ctx.env.get("ODOO_SDK_CONFIG")
    if configured:
        candidate = ctx.expand(configured)
        if candidate.is_file():
            return candidate
        directories: list[Path] = [candidate]
    else:
        directories = [ctx.expand("~/.config/odoo_sdk")]
    for directory in directories:
        for name in ("config.toml", "config.ini"):
            if (directory / name).is_file():
                return directory / name
    return None


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key)


def _toml_value(value: str, typed: bool) -> str:
    if typed and value.lower() in ("true", "false"):
        return value.lower()
    if typed and value.isdigit():
        return str(int(value))
    return json.dumps(value)


def _ini_to_toml(path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    defaults = parser.defaults()
    alias = "connection" if parser.has_section("connection") else "odoo"
    sections = [
        ("connection", alias, False),
        ("behavior", "behavior", True),
        ("model_ids", "model_ids", True),
    ]
    lines: list[str] = []
    for target, source, typed in sections:
        if not parser.has_section(source):
            continue
        lines.append(f"[{target}]")
        for key, value in parser.items(source):
            if key not in defaults:
                lines.append(f"{_toml_key(key)} = {_toml_value(value, typed)}")
        lines.append("")
    return "\n".join(lines)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    path.chmod(0o600)


def _install_config(source: Path, target: Path) -> None:
    if source.suffix == ".toml":
        _write_private(target, source.read_bytes())
        return
    staging = target.with_name(target.name + ".tmp")
    _write_private(staging, _ini_to_toml(source).encode("utf-8"))
    try:
        LocalConfig.load(path=staging, env={})
    except Exception:
        staging.unlink()
        raise
    os.replace(staging, target)


def _step_config(ctx: _Ctx) -> Step:
    target = ctx.layout.config_toml
    source = _config_source(ctx)
    if target.exists():
        return Step("config", SKIPPED, "config.toml exists")
    if source is None:
        return Step("config", SKIPPED, "no legacy config")
    if ctx.dry_run:
        return ctx.would("config", f"import {source}")
    try:
        _install_config(source, target)
    except Exception as exc:  # a bad legacy file is a finding, not a crash
        return Step("config", REFUSED, f"{source}: {exc}")
    return Step("config", DONE, f"{source} -> {target.name}")


# --- step 3: tracker -------------------------------------------------------


def _tracker_source(ctx: _Ctx) -> Path | None:
    dirs = [
        ctx.env_dir("ODOO_TASK_TRACKER_DIR"),
        ctx.expand("~/.config/odoo-task-tracker"),
        ctx.expand("~/.local/state/odoo-task-tracker"),
    ]
    return _first_file(dirs, "tracker.db")


def _task_run_job(run: sqlite3.Row) -> dict[str, Any]:
    started = _us(run["started_at"])
    closed = run["stopped_at"] or run["aborted_at"] or run["started_at"]
    ended = max(_us(closed), started)
    job: dict[str, Any] = {
        "job_id": f"run-{run['id']}",
        "batch_id": LEGACY_BATCH,
        "task_id": str(run["task_id"]),
        "repo": LEGACY_REPO,
        "state": "DONE",
        "brief": run["task_name"] or f"task {run['task_id']}",
        "submitted_us": started,
        "started_us": started,
        "ended_us": ended,
        "outcome": "FAILED" if run["aborted_at"] else "COMPLETED",
        "detail": "",
    }
    if run["state"] in _OPEN_RUN_STATES:
        job["ended_us"] = started
        job["detail"] = "legacy run was still open at migration"
    return job


def _insert_sql(table: str, columns: Iterable[str], named: bool = False) -> str:
    cols = list(columns)
    marks = ", ".join((f":{c}" if named else "?") for c in cols)
    return f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({marks})"


def _read_tracker(legacy: sqlite3.Connection) -> dict[str, list[Any]]:
    data: dict[str, list[Any]] = {
        table: [tuple(r) for r in legacy.execute(f"SELECT {cols} FROM {table}")]
        for table, cols in _TRACKER_TABLES.items()
    }
    runs = legacy.execute("SELECT * FROM task_runs ORDER BY id").fetchall()
    data["jobs"] = [_task_run_job(run) for run in runs]
    return data


def _write_tracker(ctx: _Ctx, source: Path, data: dict[str, list[Any]]) -> str:
    conn = ctx.conn()
    counts = {}
    with transaction(conn):
        for table, cols in _TRACKER_TABLES.items():
            sql = _insert_sql(table, [c.strip() for c in cols.split(",")])
            counts[table] = conn.executemany(sql, data[table]).rowcount
        keys = ("job_id", "batch_id", "task_id", "repo", "state", "brief")
        keys += ("submitted_us", "started_us", "ended_us", "outcome", "detail")
        counts["jobs"] = conn.executemany(
            _insert_sql("jobs", keys, named=True), data["jobs"]
        ).rowcount
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (MARK_TRACKER, str(source)),
        )
    return " ".join(f"{table}={n}" for table, n in counts.items())


def _step_tracker(ctx: _Ctx) -> Step:
    source = _tracker_source(ctx)
    if source is None:
        return Step("tracker", SKIPPED, "no legacy tracker.db")
    if ctx.marker(MARK_TRACKER):
        return Step("tracker", SKIPPED, "already imported")
    with closing(_ro(source)) as legacy:
        version = legacy.execute("PRAGMA user_version").fetchone()[0]
        if version != LEGACY_TRACKER_VERSION:
            return Step(
                "tracker",
                REFUSED,
                f"tracker.db is schema version {version}, expected "
                f"{LEGACY_TRACKER_VERSION}; rebuild it with the legacy tooling first",
            )
        if ctx.dry_run:
            return ctx.would("tracker", f"import {source}")
        data = _read_tracker(legacy)
    return Step("tracker", DONE, _write_tracker(ctx, source, data))


# --- step 4: jobs ----------------------------------------------------------


def _jobs_source(ctx: _Ctx) -> Path | None:
    dirs = [
        ctx.env_dir("ODOO_DISPATCH_STATE_DIR"),
        ctx.env_dir("ODOO_TASK_TRACKER_DIR"),
        ctx.expand("~/.local/state/odoo-task-tracker"),
        ctx.expand("~/.config/odoo-task-tracker"),
    ]
    return _first_file(dirs, "jobs.db")


def _read_legacy_jobs(db: sqlite3.Connection) -> list[dict[str, Any]]:
    present = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    jobs = []
    for row in db.execute("SELECT * FROM jobs ORDER BY submitted_us, job_id"):
        job = {c: (row[c] if c in present else "") for c in _LEGACY_JOB_COLUMNS}
        jobs.append({**_JOB_DEFAULTS, **job})
    return jobs


def _read_runs(path: Path) -> tuple[dict, dict, dict]:
    """Return runs by job id, envelopes by session id, artifacts by job id."""
    if not path.is_file():
        return {}, {}, {}
    with closing(_ro(path)) as db:
        runs = {
            r["job_id"]: dict(r)
            for r in db.execute("SELECT * FROM runs ORDER BY ended_us")
        }
        envelopes = {
            r["session_id"]: r["envelope"]
            for r in db.execute("SELECT session_id, envelope FROM run_envelopes")
        }
        artifacts = {
            r["job_id"]: dict(r) for r in db.execute("SELECT * FROM run_artifacts")
        }
    return runs, envelopes, artifacts


def _apply_run(job: dict[str, Any], run: dict[str, Any], envelope: str | None) -> None:
    job["branch"] = run["branch"]
    job["pr_num"] = run["pr_num"]
    job["tool_version"] = run["tool_version"]
    job["transcript_path"] = run["transcript"]
    job["envelope"] = envelope or ""
    job["session_id"] = job["session_id"] or run["session_id"]
    job["outcome"] = job["outcome"] or run["outcome"]
    if job["state"] in ("DONE", "DEAD_LETTER"):
        job["started_us"] = job["started_us"] or run["started_us"]
        job["ended_us"] = job["ended_us"] or run["ended_us"]
        job["started_us"] = max(job["started_us"], job["submitted_us"])
        job["ended_us"] = max(job["ended_us"], job["started_us"])


def _apply_artifact(job: dict[str, Any], artifact: dict[str, Any]) -> None:
    job["bundle_dir"] = artifact["dir"]
    for key in ("has_task_json", "has_session", "has_envelope", "bytes"):
        job[key] = artifact[key]
    job["harvested_us"] = artifact["harvested_us"]
    job["missing"] = artifact["missing"]


def _copy_bundle(ctx: _Ctx, job: dict[str, Any], source: Path) -> bool:
    """Copy the bundle files that matter into the new layout. ``harvest.json`` stays."""
    try:
        target = ensure_job_bundle_dir(job["task_id"], job["job_id"], ctx.home)
    except ValueError:
        return False
    files = {
        "envelope.json": "envelope.json",
        "session.jsonl": "session.jsonl",
        f"tasks/{job['task_id']}/task.json": "task.json",
    }
    for rel, name in files.items():
        if (source / rel).is_file() and not (target / name).exists():
            shutil.copy2(source / rel, target / name)
    job["bundle_dir"] = str(target)
    return True


def _bundle_source(ctx: _Ctx, job: dict[str, Any]) -> Path:
    if job["bundle_dir"]:
        return ctx.expand(job["bundle_dir"])
    root = ctx.env_dir("DISPATCH_ARTIFACTS_DIR") or ctx.expand(
        "~/.config/odoo-dev/dispatch/artifacts"
    )
    return root / job["job_id"]


def _build_jobs(ctx: _Ctx, source: Path, legacy: list[dict[str, Any]]):
    runs, envelopes, artifacts = _read_runs(source.with_name("runs.db"))
    bundles = 0
    for job in legacy:
        run = runs.get(job["job_id"])
        if run:
            _apply_run(job, run, envelopes.get(run["session_id"]))
        if job["job_id"] in artifacts:
            _apply_artifact(job, artifacts[job["job_id"]])
        origin = _bundle_source(ctx, job)
        if origin.is_dir() and _copy_bundle(ctx, job, origin):
            bundles += 1
    return legacy, bundles


def _step_jobs(ctx: _Ctx) -> Step:
    source = _jobs_source(ctx)
    if source is None:
        return Step("jobs", SKIPPED, "no legacy jobs.db")
    if ctx.marker(MARK_JOBS):
        return Step("jobs", SKIPPED, "already imported")
    with closing(_ro(source)) as db:
        running = db.execute("SELECT COUNT(*) FROM jobs WHERE state='RUNNING'")
        if running.fetchone()[0]:
            return Step("jobs", REFUSED, "a job is RUNNING; drain the dispatcher first")
        legacy = _read_legacy_jobs(db)
    if ctx.dry_run:
        return ctx.would("jobs", f"import {len(legacy)} job(s) from {source}")
    rows, bundles = _build_jobs(ctx, source, legacy)
    conn = ctx.conn()
    with transaction(conn):
        sql = _insert_sql("jobs", [*_LEGACY_JOB_COLUMNS, *_JOB_DEFAULTS], named=True)
        imported = conn.executemany(sql, rows).rowcount
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (MARK_JOBS, str(source)),
        )
    return Step("jobs", DONE, f"jobs={imported} bundles={bundles}")


# --- step 5: repo-map ------------------------------------------------------


def _has_legacy_keys(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "_doc" in data:
        return True
    projects = data.get("projects")
    for entry in projects.values() if isinstance(projects, dict) else ():
        if not isinstance(entry, dict):
            continue
        flow = entry.get("branch_flow")
        flow = flow if isinstance(flow, list) else []
        if _LEGACY_MAP_KEYS & entry.keys() or any(
            isinstance(f, str) and f.startswith(":task") for f in flow
        ):
            return True
    return False


def _hoist(repo_map):
    """Move a release assignee or reviewer shared by every project into defaults."""
    for field in ("release_assignee", "release_reviewer"):
        values = {getattr(p, field) for p in repo_map.projects.values()} - {""}
        if len(values) == 1 and not getattr(repo_map.defaults, field):
            repo_map.defaults = replace(repo_map.defaults, **{field: values.pop()})
            repo_map.projects = {
                name: replace(p, **{field: ""}) for name, p in repo_map.projects.items()
            }
    return repo_map


def _step_repo_map(ctx: _Ctx) -> Step:
    target = ctx.layout.repo_map
    if not target.is_file():
        return Step("repo-map", SKIPPED, "no repo-map.json")
    try:
        text = target.read_text("utf-8")
        legacy = _has_legacy_keys(json.loads(text))
        repo_map = _hoist(from_json(text)) if legacy else None
    except (OSError, ValueError) as exc:
        return Step("repo-map", REFUSED, f"{target.name}: {exc}")
    if repo_map is None:
        return Step("repo-map", SKIPPED, "already in the new schema")
    errors = validate(repo_map)
    if errors:
        return Step("repo-map", REFUSED, "; ".join(errors))
    if ctx.dry_run:
        return ctx.would("repo-map", f"rewrite {len(repo_map.projects)} project(s)")
    save_repo_map(repo_map, target)
    return Step("repo-map", DONE, f"{len(repo_map.projects)} project(s), .bak kept")


# --- step 6: orchestrator --------------------------------------------------


def _copy_absent(source: Path, target: Path) -> int:
    if target.exists():
        return 0
    shutil.copy2(source, target)
    return 1


def _copy_drafts(ctx: _Ctx, source: Path, notes: list[str]) -> int:
    copied = 0
    for draft in sorted(source.glob("briefs/*/draft-*.md")):
        task = draft.parent.name
        if not _DRAFT.match(draft.name):
            continue
        if not task.isdigit():
            notes.append(f"skipped non-numeric task {task!r}")
            continue
        folder = ensure_task_dir(task, ctx.home)
        copied += _copy_absent(draft, folder / draft.name)
    return copied


def _copy_approved(ctx: _Ctx, source: Path, notes: list[str]) -> int:
    copied = 0
    for ref in sorted(source.glob("approved/*.txt")):
        task = ref.stem
        draft = Path(ref.read_text("utf-8", errors="replace").strip())
        if not task.isdigit():
            notes.append(f"skipped non-numeric task {task!r}")
        elif draft.is_file():
            ensure_task_dir(task, ctx.home)
            copied += _copy_absent(draft, brief_path(task, ctx.home))
    return copied


def _ids(path: Path) -> list[str]:
    try:
        return path.read_text("utf-8", errors="replace").split()
    except OSError:
        return []


def _mark_jobs(ctx: _Ctx, source: Path, notes: list[str]) -> int:
    conn = ctx.conn()
    known = {row[0] for row in conn.execute("SELECT job_id FROM jobs")}
    store = JobStore(conn)
    changed = 0
    for name, mark in (
        ("handled.txt", store.mark_reported),
        ("friction-done.txt", store.mark_friction_recorded),
    ):
        ids = _ids(source / name)
        unknown = [i for i in ids if i not in known]
        if unknown:
            notes.append(f"{len(unknown)} unknown id(s) in {name}")
        changed += mark([i for i in ids if i in known], now_us=ctx.now_us)
    for batch in _ids(source / "batches-done.txt"):
        rows = conn.execute("SELECT job_id FROM jobs WHERE batch_id=?", (batch,))
        changed += store.mark_reported([r[0] for r in rows], now_us=ctx.now_us)
    return changed


def _copy_friction(ctx: _Ctx, source: Path) -> int:
    notes = sorted(source.glob("friction/*.md"))
    if not notes:
        return 0
    folder = ctx.home / "friction-legacy"
    folder.mkdir(exist_ok=True)
    return sum(_copy_absent(note, folder / note.name) for note in notes)


def _step_orchestrator(ctx: _Ctx) -> Step:
    source = ctx.env_dir("ODOO_ORCHESTRATOR_HOME") or ctx.expand(
        "~/.local/state/odoo-orchestrator"
    )
    if not source.is_dir():
        return Step("orchestrator", SKIPPED, "no legacy orchestrator home")
    if ctx.dry_run:
        return ctx.would("orchestrator", f"import {source}")
    notes: list[str] = []
    counts = {
        "drafts": _copy_drafts(ctx, source, notes),
        "briefs": _copy_approved(ctx, source, notes),
        "marked": _mark_jobs(ctx, source, notes),
        "friction": _copy_friction(ctx, source),
    }
    detail = " ".join(f"{k}={v}" for k, v in counts.items())
    if notes:
        detail += "; " + "; ".join(notes)
    return Step("orchestrator", DONE if any(counts.values()) else SKIPPED, detail)


# --- step 7: usage ---------------------------------------------------------


def _step_usage(ctx: _Ctx) -> Step:
    source = ctx.env_dir("USAGE_LOG_DIR") or ctx.expand("~/.local/state/usage-log")
    pairs = [
        (source / "usage.db", ctx.layout.usage_db),
        (source / "declarations.db", ctx.home / "declarations.db"),
    ]
    todo = [(src, dst) for src, dst in pairs if src.is_file() and not dst.exists()]
    if not todo:
        return Step("usage", SKIPPED, "nothing to copy")
    if ctx.dry_run:
        return ctx.would("usage", "copy " + ", ".join(s.name for s, _ in todo))
    for src, dst in todo:
        shutil.copy2(src, dst)
    return Step("usage", DONE, "copied " + ", ".join(s.name for s, _ in todo))


_STEPS: tuple[Callable[[_Ctx], Step], ...] = (
    _step_home,
    _step_config,
    _step_tracker,
    _step_jobs,
    _step_repo_map,
    _step_orchestrator,
    _step_usage,
)


def migrate(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    dry_run: bool = False,
    now_us: int | None = None,
) -> MigrateReport:
    """Import every legacy store into ``home``. See the module docstring."""
    env = os.environ if env is None else env
    root = resolve_home(env) if home is None else home
    ctx = _Ctx(root, env, dry_run, now_us)
    steps: list[Step] = []
    try:
        for run in _STEPS:
            steps.append(run(ctx))
    finally:
        ctx.close()
    return MigrateReport(root, dry_run, tuple(steps))


def render_migrate(report: MigrateReport) -> str:
    suffix = " (dry run)" if report.dry_run else ""
    lines = [f"ODOO_DEV_HOME={report.home}{suffix}"]
    lines += [f"{s.action:<8} {s.name:<12} {s.detail}" for s in report.steps]
    refused = sum(1 for s in report.steps if s.action == REFUSED)
    lines.append("ok" if report.ok else f"{refused} refused")
    return "\n".join(lines)

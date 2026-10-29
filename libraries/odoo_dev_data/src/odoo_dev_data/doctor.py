"""Read-only report of the ``ODOO_DEV_HOME`` state root.

``python -m odoo_dev_data doctor [--json]``. The report never creates a file or
directory and never opens ``state.db`` for writing.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import LocalConfig
from .db.engine import schema_version
from .db.migrations import MIGRATIONS
from .home import ENV_HOME, Paths, paths, resolve_home
from .migrate import migrate, render_migrate

OK, NOTE, FAIL = "ok", "note", "fail"

#: Filesystem types on which SQLite WAL is not coherent across the share.
_SHARE_FSTYPES = frozenset({"fuse.grpcfuse", "virtiofs", "9p", "osxfs", "fakeowner"})
_PROC_MOUNTS = Path("/proc/mounts")


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "ok" | "note" | "fail"
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    home: Path
    env_set: bool
    checks: tuple[Check, ...]
    schema_version: int | None

    @property
    def ok(self) -> bool:
        return all(check.status != FAIL for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "env_set": self.env_set,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
            "schema_version": self.schema_version,
            "ok": self.ok,
        }


def _home_checks(home: Path) -> list[Check]:
    if not home.is_dir():
        return [Check("home", FAIL, f"{home} missing: created on first use")]
    checks = [Check("home", OK, str(home))]
    mode = stat.S_IMODE(home.stat().st_mode)
    if mode & 0o077:
        checks.append(Check("home mode", NOTE, f"{mode:04o}: readable by group/other"))
    else:
        checks.append(Check("home mode", OK, f"{mode:04o}"))
    return checks


def _config_check(path: Path, env: Mapping[str, str]) -> Check:
    present = path.is_file()
    try:
        connection = LocalConfig.load(path=path, env=env).connection
    except Exception as exc:  # any parse or value error is the finding
        return Check("config.toml", FAIL, str(exc))
    if not connection.get("url"):
        if not present:
            return Check(
                "config.toml",
                NOTE,
                "missing: connection comes from environment variables",
            )
        return Check("config.toml", NOTE, "no url resolved")
    detail = " ".join(f"{k}={connection.get(k) or '-'}" for k in _CONN_KEYS)
    return Check("config.toml", OK, detail)


_CONN_KEYS = ("url", "db", "username")


def _github_account_check(path: Path, env: Mapping[str, str]) -> Check:
    """Report the configured GitHub account and whether gh holds a token for it."""
    try:
        account = LocalConfig.load(path=path, env=env).github_account
    except Exception:  # config.toml reports its own parse error
        return Check("github account", NOTE, "config.toml unreadable")
    if not account:
        return Check(
            "github account",
            NOTE,
            "[github] account unset; agents use the active gh account",
        )
    try:
        done = subprocess.run(
            ["gh", "auth", "token", "--user", account],
            capture_output=True,
            timeout=10,
            check=False,
        )
        found = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        found = False
    if found:
        return Check("github account", OK, f"{account}: gh has a token")
    return Check("github account", FAIL, f"{account}: gh has no token for this login")


def _repo_map_check(path: Path) -> Check:
    if not path.is_file():
        return Check("repo-map.json", NOTE, "missing")
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return Check("repo-map.json", FAIL, str(exc))
    if isinstance(data, dict) and "projects" in data:
        return Check("repo-map.json", OK, f"{len(data['projects'])} projects")
    return Check("repo-map.json", OK, "present")


def _table_counts(conn: sqlite3.Connection) -> str:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return " ".join(
        f"{name}={conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]}"
        for name in ("events", "jobs")
        if name in tables
    )


def _state_db_check(path: Path) -> tuple[Check, int | None]:
    name = "state.db"
    if not path.is_file():
        return Check(name, NOTE, "missing: created on first connect"), None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                return Check(name, FAIL, f"quick_check: {quick}"), None
            version = schema_version(conn)
            counts = _table_counts(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return Check(name, FAIL, str(exc)), None
    wanted = len(MIGRATIONS)
    extra = f" {counts}" if counts else ""
    if version == wanted:
        return Check(name, OK, f"schema {version}/{wanted}{extra}"), version
    if version < wanted:
        detail = (
            f"pending migrations: applied on next connect (schema {version}/{wanted})"
        )
        return Check(name, NOTE, detail + extra), version
    return Check(name, FAIL, f"schema ahead of this code ({version}/{wanted})"), version


def _usage_db_check(path: Path) -> Check:
    if not path.is_file():
        return Check("usage.db", NOTE, "missing")
    return Check("usage.db", OK, f"{path.stat().st_size} bytes")


def _dir_check(name: str, path: Path) -> Check:
    if not path.is_dir():
        return Check(name, NOTE, "missing")
    return Check(name, OK, f"{sum(1 for _ in path.iterdir())} entries")


def _unescape_mount(field: str) -> str:
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _mount_fstype(path: Path) -> str | None:
    """Return the fstype of the longest mount point containing ``path``."""
    try:
        lines = _PROC_MOUNTS.read_text("utf-8").splitlines()
    except OSError:
        return None
    target = path.resolve()
    best: tuple[int, str] | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = Path(_unescape_mount(parts[1]))
        if (mount == target or mount in target.parents) and (
            best is None or len(mount.parts) > best[0]
        ):
            best = (len(mount.parts), parts[2])
    return best[1] if best else None


def _wal_check(home: Path) -> Check | None:
    fstype = _mount_fstype(home)
    if fstype is None:
        return None
    if fstype in _SHARE_FSTYPES:
        return Check(
            "wal",
            NOTE,
            "SQLite WAL is not coherent across a Docker Desktop share; "
            "run the suite inside the container",
        )
    return Check("wal", OK, fstype)


def _file_checks(
    layout: Paths, env: Mapping[str, str]
) -> tuple[list[Check], int | None]:
    state, version = _state_db_check(layout.state_db)
    checks = [
        _config_check(layout.config_toml, env),
        _github_account_check(layout.config_toml, env),
        _repo_map_check(layout.repo_map),
        state,
        _usage_db_check(layout.usage_db),
        _dir_check("tasks/", layout.tasks),
        _dir_check("locks/", layout.locks),
    ]
    return checks, version


def doctor(
    home: Path | None = None, env: Mapping[str, str] | None = None
) -> DoctorReport:
    """Inspect the state root. Strictly read-only."""
    env = os.environ if env is None else env
    root = resolve_home(env) if home is None else home
    layout = paths(root)
    checks = _home_checks(root)
    file_checks, version = _file_checks(layout, env)
    checks += file_checks
    wal = _wal_check(root)
    if wal is not None:
        checks.append(wal)
    return DoctorReport(root, bool(env.get(ENV_HOME)), tuple(checks), version)


def render(report: DoctorReport) -> str:
    origin = "from environment" if report.env_set else "default"
    lines = [f"{ENV_HOME}={report.home} ({origin})"]
    lines += [f"{c.status:<4} {c.name:<14} {c.detail}" for c in report.checks]
    problems = sum(1 for c in report.checks if c.status == FAIL)
    lines.append("ok" if report.ok else f"{problems} problem(s)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odoo_dev_data")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("doctor", help="read-only report of the state root")
    cmd.add_argument("--json", action="store_true", help="print JSON")
    cmd.add_argument("--migrate", action="store_true", help="import every legacy store")
    cmd.add_argument(
        "--dry-run", action="store_true", help="with --migrate: change nothing"
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.migrate:
        parser.error("--dry-run needs --migrate")
    if args.migrate:
        result = migrate(dry_run=args.dry_run)
        print(
            json.dumps(result.to_dict(), indent=2)
            if args.json
            else render_migrate(result)
        )
        return 0 if result.ok else 1
    report = doctor()
    print(json.dumps(report.to_dict(), indent=2) if args.json else render(report))
    return 0 if report.ok else 1

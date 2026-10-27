"""Usage log: one SQLite line per use of a feature, one declaration per feature.

Stdlib only and independent of every other package. It writes ``usage.db`` and
``declarations.db`` under ``$ODOO_DEV_HOME`` (default ``~/.config/odoo-dev``).
``ODOO_DEV_USAGE_LOG=0`` turns logging off. It never creates the root directory:
when the root is missing every function does nothing. A write that fails never
raises: it prints one stderr line and returns False.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

__all__ = ["declare", "normalise", "record"]

ENV_HOME = "ODOO_DEV_HOME"
ENV_SWITCH = "ODOO_DEV_USAGE_LOG"
DEFAULT_HOME = "~/.config/odoo-dev"
ENV_JOB_ID = "ODOO_JOB_ID"
USAGE_DB = "usage.db"
DECLARATIONS_DB = "declarations.db"
BUSY_TIMEOUT_MS = 5000

_USAGE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS usage" " (ts TEXT, kind TEXT, name TEXT, context TEXT)",
    "CREATE INDEX IF NOT EXISTS usage_kind_name_ts ON usage (kind, name, ts)",
)
_DECLARATIONS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS declarations"
    " (kind TEXT, name TEXT, declared_at TEXT, context TEXT,"
    " PRIMARY KEY (kind, name))",
)
_INSERT_USAGE = "INSERT INTO usage (ts, kind, name, context) VALUES (?, ?, ?, ?)"
_UPSERT_DECLARATION = (
    "INSERT INTO declarations (kind, name, declared_at, context)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT (kind, name) DO UPDATE SET"
    " declared_at = excluded.declared_at, context = excluded.context"
)


def normalise(kind: str, name: str) -> tuple[str, str]:
    """Return the one canonical ``(kind, name)`` used by both stores.

    ``kind`` is lower-cased; ``name`` loses a leading ``/`` (a slash command
    typed as ``/odoo-dev:plan`` is the feature ``odoo-dev:plan``) and has its
    inner whitespace collapsed. Case in ``name`` is kept.
    """
    clean_name = " ".join(str(name).split()).lstrip("/")
    return str(kind).strip().lower(), clean_name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _directory() -> str | None:
    """Return the state root, or None when logging is off or the root is missing."""
    if os.environ.get(ENV_SWITCH) == "0":
        return None
    root = os.path.expanduser(os.environ.get(ENV_HOME) or DEFAULT_HOME)
    return root if os.path.isdir(root) else None


def _dump(context: Mapping[str, Any]) -> str:
    return json.dumps(dict(context), default=str, sort_keys=True)


def _write(directory: str, filename: str, schema: tuple, sql: str, rows: list) -> None:
    """One connection, one short ``BEGIN IMMEDIATE`` transaction, close."""
    conn = sqlite3.connect(
        os.path.join(directory, filename),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in schema:
                conn.execute(statement)
            conn.executemany(sql, rows)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _safely(what: str, directory: str, *args: Any) -> bool:
    try:
        _write(directory, *args)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"usage-log: dropped {what}: {exc}", file=sys.stderr)
        return False
    return True


def record(kind: str, name: str, /, **context: Any) -> bool:
    """Write one usage line. Return True when written, False when dropped."""
    directory = _directory()
    if directory is None:
        return False
    kind, name = normalise(kind, name)
    job_id = os.environ.get(ENV_JOB_ID, "").strip()
    if job_id and "job_id" not in context:
        context["job_id"] = job_id
    row = (_now(), kind, name, _dump(context))
    return _safely(
        f"{kind} {name}", directory, USAGE_DB, _USAGE_SCHEMA, _INSERT_USAGE, [row]
    )


def _declaration_row(item: Any, declared_at: str) -> tuple[str, str, str, str]:
    if isinstance(item, Mapping):
        kind, name, context = item["kind"], item["name"], item.get("context") or {}
    else:
        kind, name, *rest = item
        context = rest[0] if rest else {}
    kind, name = normalise(kind, name)
    return kind, name, declared_at, _dump(context)


def declare(items: Iterable[Any]) -> bool:
    """Upsert declarations in one transaction.

    Each item is ``{"kind", "name", "context"?}`` or ``(kind, name[, context])``.
    """
    directory = _directory()
    if directory is None:
        return False
    declared_at = _now()
    try:
        rows = [_declaration_row(item, declared_at) for item in items]
    except (KeyError, TypeError, ValueError) as exc:
        print(f"usage-log: dropped declarations: bad item: {exc!r}", file=sys.stderr)
        return False
    if not rows:
        return True
    return _safely(
        f"{len(rows)} declarations",
        directory,
        DECLARATIONS_DB,
        _DECLARATIONS_SCHEMA,
        _UPSERT_DECLARATION,
        rows,
    )

"""Shared test helpers for provisioning a tracker state database (#369).

The SDK no longer creates schema as a side effect of opening a connection: the
central tracker DB is host-provisioned and bind-mounted, and
:meth:`LocalStateClient._connect` raises :class:`TrackerStateMissingError` when
the file is absent. Tests that need a working DB therefore apply the schema
explicitly, the same way the host init script does — via the SDK's own
:func:`odoo_sdk.state.create_schema` over :data:`odoo_sdk.state.SCHEMA_DDL`.

Use :func:`make_state_db` (or :func:`provision_schema` for an existing path) so
every test starts from a schema-ready DB without depending on the removed
implicit-creation behavior.
"""

import sqlite3
import tempfile
from pathlib import Path
from typing import Optional, Union

from odoo_sdk.state import LocalStateClient, create_schema


def provision_schema(db_path: Union[str, Path]) -> Path:
    """Apply the tracker schema to ``db_path`` (creating the file), return it.

    Idempotent, like the host init step: re-provisioning an existing DB is a
    harmless no-op because :func:`create_schema`'s DDL is ``IF NOT EXISTS``.
    """
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    try:
        create_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def make_state_db_path() -> Path:
    """Return a fresh temp file path with the tracker schema already applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return provision_schema(tmp.name)


def make_state_db(db_path: Optional[Union[str, Path]] = None) -> LocalStateClient:
    """Return a :class:`LocalStateClient` bound to a schema-provisioned DB.

    With no ``db_path`` a fresh temp DB is provisioned; with one, the schema is
    applied to that path first so the returned client is immediately usable.
    """
    if db_path is None:
        db_path = make_state_db_path()
    else:
        provision_schema(db_path)
    return LocalStateClient(db_path=db_path)

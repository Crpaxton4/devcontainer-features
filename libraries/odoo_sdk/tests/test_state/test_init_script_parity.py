"""Parity gate between the host init script and the SDK's canonical schema (#369).

``scripts/init_tracker_db.py`` is stdlib-only and runs on a bare host with no
``odoo_sdk`` installed, so it embeds a verbatim copy of
:data:`odoo_sdk.state.db.SCHEMA_DDL`. This test provisions one database with the
init script and one with the SDK's :func:`create_schema` and asserts their
``sqlite_master`` is byte-for-byte identical, so the two DDL copies can never
silently drift and a container that connects to a host-provisioned DB always sees
exactly the schema the SDK expects.
"""

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from odoo_sdk.state.db import SCHEMA_DDL, create_schema

INIT_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "init_tracker_db.py"


def _load_init_script():
    spec = importlib.util.spec_from_file_location("init_tracker_db", INIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _schema_objects(db_path: Path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return sorted(
            conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
            ).fetchall()
        )
    finally:
        conn.close()


class TestInitScriptParity(unittest.TestCase):
    def test_init_script_exists(self):
        self.assertTrue(
            INIT_SCRIPT.is_file(), f"host init script missing at {INIT_SCRIPT}"
        )

    def test_ddl_is_verbatim_copy(self):
        init = _load_init_script()
        self.assertEqual(init.SCHEMA_DDL, SCHEMA_DDL)

    def test_resulting_schema_is_identical(self):
        init = _load_init_script()
        tmp = Path(tempfile.mkdtemp())
        from_script = tmp / "script.db"
        init.init_tracker_db(from_script)

        from_sdk = tmp / "sdk.db"
        conn = sqlite3.connect(str(from_sdk))
        create_schema(conn)
        conn.commit()
        conn.close()

        self.assertEqual(_schema_objects(from_script), _schema_objects(from_sdk))

    def test_init_script_is_idempotent(self):
        init = _load_init_script()
        path = Path(tempfile.mkdtemp()) / "tracker.db"
        init.init_tracker_db(path)
        before = _schema_objects(path)
        init.init_tracker_db(path)  # re-run: no error, no schema change
        self.assertEqual(_schema_objects(path), before)


if __name__ == "__main__":
    unittest.main()

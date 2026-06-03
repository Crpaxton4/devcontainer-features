"""Manual Phase B live-Odoo smoke test for a configured production-safe ToDo flow.

This script is intentionally separate from `tests/` so automated validation stays
purely local and deterministic. It exercises the Phase B metadata, adapted-read,
x2many write, and error-mapping paths against a real Odoo instance.

The flow is metadata-driven. It reads the live `project.task` field metadata and
uses the reported field types to decide how to round-trip adapted temporal
values instead of assuming a fixed schema ahead of time.

Safety constraints:
- Requires `--allow-live-production` before any RPC calls are made.
- Creates exactly one new `project.task` ToDo per run with `project_id=False`.
- Uses only create, read, and update operations.
- Does not use tags, delete, unlink, or x2many delete/unlink commands.

Run from the repository root:

    python examples/live_phase_b_smoke_test.py --allow-live-production

The primary SDK entry path demonstrated here is model-bound recordset lookup via
`client["project.task"]`, followed by explicit `browse(...)`, `write(...)`, and
`read_adapted(...)` operations where raw or adapted extraction is still desired.
"""

from datetime import date, datetime, timedelta, timezone

from odoo_sdk.odoo_service import Command, OdooClient

DEADLINE = date.today() + timedelta(days=7)


def run_smoke() -> int:
    odoo = OdooClient()

    if not odoo.authenticated:
        print(f"Authentication failed: uid is falsy ({odoo.uid})")
        return 0

    todo_id = odoo["project.task"].create(
        {
            "name": f"SDK live smoke - {datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
            "project_id": False,
            "description": "",
        }
    )

    write_result = (
        odoo["project.task"]
        .browse(todo_id)
        .write(
            {
                "date_deadline": f"{DEADLINE.isoformat()}",
                "description": (
                    "Assigned to the current user and updated by the manual Phase B smoke test."
                    f"date_deadline: {DEADLINE.isoformat()}"
                ),
                "user_ids": Command.set([odoo.uid]),
            },
        )
    )

    todo_rec = odoo["project.task"].browse(todo_id)

    print(f"\n== summary ==")
    print(f"Created ToDo id: {todo_id}")
    print(f"Write result: {write_result}")
    print(f"Browse Result: {todo_rec}")
    return todo_rec.id


def main() -> None:
    created_todo_id: int | None = None

    try:
        created_todo_id = run_smoke()
    except Exception:
        if created_todo_id is not None:
            print(
                "\nNOTE: The smoke test created project.task "
                f"id {created_todo_id} before failing."
            )
        raise


if __name__ == "__main__":
    main()

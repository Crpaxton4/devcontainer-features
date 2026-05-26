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
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from pprint import pprint
from typing import Any
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPOSITORY_ROOT / ".odoo_sdk.ini"
DEFAULT_TODO_NAME_PREFIX = "SDK Phase B live smoke"
DEFAULT_TODO_DESCRIPTION = (
    "Manual Phase B live smoke test record. "
    "Created intentionally without automatic cleanup."
)
METADATA_FIELDS = [
    "project_id",
    "create_uid",
    "user_ids",
    "date_deadline",
    "write_date",
]
ADAPTED_READ_FIELDS = [
    "id",
    "name",
    "project_id",
    "create_uid",
    "user_ids",
    "date_deadline",
    "write_date",
    "description",
]


if str(REPOSITORY_ROOT) not in sys.path:
    # Support direct execution from a source checkout before installation.
    sys.path.insert(0, str(REPOSITORY_ROOT))


from odoo_sdk.odoo_service import (  # noqa: E402
    OdooAuthenticationError,
    OdooClient,
    OdooConnectionSettings,
    X2ManyCommand,
)
from odoo_sdk.odoo_service.field_values import (
    RelationCollection,
    RelationValue,
)  # noqa: E402


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a manual live-Odoo smoke test for the Phase B metadata, "
            "adaptation, x2many, and error-mapping paths against a live Odoo "
            "instance using whatever temporal field metadata it reports."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--allow-live-production",
        action="store_true",
        help=(
            "Required acknowledgement before the script talks to the configured "
            "Odoo instance and performs metadata-driven checks."
        ),
    )
    parser.add_argument(
        "--config-path",
        default=None,
        help="Optional path to an Odoo SDK INI file. Defaults to the repository-local .odoo_sdk.ini when present.",
    )
    parser.add_argument(
        "--todo-name-prefix",
        default=DEFAULT_TODO_NAME_PREFIX,
        help="Fixed prefix used for the new ToDo created by this manual smoke run.",
    )
    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip the deliberate bad-password authentication check when needed.",
    )
    return parser


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_section(title: str) -> None:
    print(f"\n== {title} ==")


def print_pass(message: str) -> None:
    print(f"PASS: {message}")


def resolve_config_path(config_path: str | None) -> str | None:
    if config_path is not None:
        return config_path
    if DEFAULT_CONFIG_PATH.is_file():
        return str(DEFAULT_CONFIG_PATH)
    return None


def load_connection_settings(config_path: str | None) -> OdooConnectionSettings | None:
    try:
        return OdooConnectionSettings.from_sources(
            config_path=resolve_config_path(config_path)
        )
    except ValueError as exc:
        print_section("configuration")
        print(f"SKIP: {exc}")
        return None


def build_client(settings: OdooConnectionSettings) -> OdooClient:
    return OdooClient(
        url=settings.url,
        db=settings.db,
        username=settings.username,
        password=settings.password,
    )


def build_todo_name(prefix: str) -> str:
    return f"{prefix} {datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"


def require_field_metadata(
    metadata_by_field: dict[str, dict[str, Any]],
    field_name: str,
    *,
    expected_types: tuple[str, ...],
    expected_relation: str | None = None,
) -> dict[str, Any]:
    observed_metadata = metadata_by_field.get(field_name)
    require(
        isinstance(observed_metadata, dict),
        f"fields_get() did not return metadata for {field_name!r}.",
    )

    observed_type = observed_metadata.get("type")
    if observed_type not in expected_types:
        expectation = ", ".join(repr(item) for item in expected_types)
        raise AssertionError(
            f"Unexpected metadata for {field_name}: {observed_metadata!r}. "
            f"Expected type to be one of {expectation}."
        )

    if (
        expected_relation is not None
        and observed_metadata.get("relation") != expected_relation
    ):
        raise AssertionError(
            f"Unexpected metadata for {field_name}: {observed_metadata!r}. "
            f"Expected relation {expected_relation!r}."
        )

    return observed_metadata


def build_deadline_roundtrip(deadline_metadata: dict[str, Any]) -> tuple[str, date | datetime]:
    deadline_type = deadline_metadata.get("type")

    if deadline_type == "date":
        target_deadline = date.today() + timedelta(days=7)
        return target_deadline.isoformat(), target_deadline

    require(
        deadline_type == "datetime",
        f"date_deadline should be a date or datetime field, got {deadline_metadata!r}.",
    )
    target_deadline = datetime.combine(
        date.today() + timedelta(days=7),
        time(hour=12, minute=0),
        tzinfo=timezone.utc,
    )
    return target_deadline.strftime("%Y-%m-%d %H:%M:%S"), target_deadline


def verify_metadata(client: OdooClient) -> dict[str, dict[str, Any]]:
    print_section("metadata retrieval")
    client.env.clear_metadata_cache("project.task")
    refreshed_metadata = client.env.get_field_metadata(
        "project.task",
        fields=METADATA_FIELDS,
        attributes=["type", "relation"],
        refresh=True,
    )
    cached_metadata = client.env.get_field_metadata(
        "project.task",
        fields=METADATA_FIELDS,
        attributes=["type", "relation"],
    )

    print("Observed project.task metadata:")
    pprint(refreshed_metadata)

    require(refreshed_metadata == cached_metadata, "Repeated metadata reads diverged.")
    require_field_metadata(
        refreshed_metadata,
        "project_id",
        expected_types=("many2one",),
    )
    require_field_metadata(
        refreshed_metadata,
        "create_uid",
        expected_types=("many2one",),
        expected_relation="res.users",
    )
    require_field_metadata(
        refreshed_metadata,
        "user_ids",
        expected_types=("many2many",),
        expected_relation="res.users",
    )
    deadline_metadata = require_field_metadata(
        refreshed_metadata,
        "date_deadline",
        expected_types=("date", "datetime"),
    )
    require_field_metadata(
        refreshed_metadata,
        "write_date",
        expected_types=("datetime",),
    )

    print_pass(
        "Fetched project.task metadata through the Phase B metadata path and "
        f"detected date_deadline as {deadline_metadata['type']!r}."
    )
    return refreshed_metadata


def create_todo(project_task_model: Any, todo_name_prefix: str) -> int:
    print_section("todo creation")
    todo_name = build_todo_name(todo_name_prefix)
    todo_id = project_task_model.create(
        {
            "name": todo_name,
            "project_id": False,
            "description": DEFAULT_TODO_DESCRIPTION,
        }
    )

    require(
        isinstance(todo_id, int) and todo_id > 0,
        "create() should return a positive integer id.",
    )
    print(f"Created project.task ToDo id: {todo_id}")
    print(f"Created project.task ToDo name: {todo_name}")
    print_pass("Created one fresh project.task ToDo with project_id=False.")
    return todo_id


def update_todo(
    project_task_model: Any,
    todo_id: int,
    uid: int,
    deadline_write_value: str,
) -> None:
    print_section("todo update")
    write_result = project_task_model.write(
        todo_id,
        {
            "date_deadline": deadline_write_value,
            "description": (
                f"{DEFAULT_TODO_DESCRIPTION} Assigned to the current user and updated "
                "by the manual Phase B smoke test."
            ),
            "user_ids": X2ManyCommand.set([uid]),
        },
    )

    require(
        write_result is True, "write() should return True for the live smoke update."
    )
    print_pass(
        "Updated the ToDo through the write path and exercised X2ManyCommand.set()."
    )


def verify_adapted_read(
    project_task_model: Any,
    todo_id: int,
    uid: int,
    deadline_metadata: dict[str, Any],
    expected_deadline: date | datetime,
) -> dict[str, Any]:
    print_section("adapted read")
    records = project_task_model.read_adapted(todo_id, ADAPTED_READ_FIELDS)
    require(
        len(records) == 1,
        "read_adapted() should return exactly one record for the created ToDo.",
    )

    record = records[0]
    create_uid = record["create_uid"]
    user_ids = record["user_ids"]
    deadline_value = record["date_deadline"]
    write_date_value = record["write_date"]

    require(record["project_id"] is None, "project_id=False should adapt to None.")
    require(
        isinstance(create_uid, RelationValue),
        "create_uid should adapt to RelationValue.",
    )
    require(
        create_uid.model_name == "res.users",
        "create_uid should adapt with the res.users model name.",
    )
    require(create_uid.id > 0, "create_uid should expose a positive user id.")
    require(
        isinstance(user_ids, RelationCollection),
        "user_ids should adapt to RelationCollection.",
    )
    require(
        user_ids.model_name == "res.users",
        "user_ids should adapt with the res.users model name.",
    )
    require(
        uid in user_ids.ids, "user_ids should include the current authenticated user."
    )
    deadline_type = deadline_metadata["type"]
    if deadline_type == "date":
        require(
            isinstance(deadline_value, date) and not isinstance(deadline_value, datetime),
            "date_deadline should adapt to datetime.date when metadata reports a date field.",
        )
    else:
        require(
            isinstance(deadline_value, datetime),
            "date_deadline should adapt to datetime.datetime when metadata reports a datetime field.",
        )
        require(
            deadline_value.tzinfo == timezone.utc,
            "date_deadline datetime values should normalize to UTC.",
        )
    require(
        deadline_value == expected_deadline,
        "date_deadline did not round-trip through Phase B adaptation.",
    )
    require(
        isinstance(write_date_value, datetime),
        "write_date should adapt to datetime.datetime.",
    )
    require(
        write_date_value.tzinfo == timezone.utc, "write_date should normalize to UTC."
    )

    pprint(record)
    print_pass(
        "Validated adapted relation and temporal fields from the live record."
    )
    return record


def verify_authentication_error(settings: OdooConnectionSettings) -> None:
    print_section("mapped auth error")
    invalid_client = OdooClient(
        url=settings.url,
        db=settings.db,
        username=settings.username,
        password=f"{settings.password}-invalid-live-smoke",
    )

    try:
        _ = invalid_client.uid
    except OdooAuthenticationError as exc:
        detail = exc.detail or exc.fault_string or str(exc)
        print(f"Observed authentication failure detail: {detail}")
        print_pass(
            "Mapped a deliberate bad-password login attempt to OdooAuthenticationError."
        )
        return

    raise AssertionError(
        "Expected a deliberate bad-password login attempt to raise OdooAuthenticationError."
    )


def run_smoke(arguments: argparse.Namespace) -> int:
    if not arguments.allow_live_production:
        raise SystemExit(
            "Refusing to run against the configured Odoo instance without "
            "--allow-live-production."
        )

    settings = load_connection_settings(arguments.config_path)
    if settings is None:
        return 0

    print_section("live connection")
    client = build_client(settings)
    uid = client.uid
    print_pass(f"Authenticated successfully as Odoo uid {uid}.")

    project_task_model = client.env["project.task"]
    metadata = verify_metadata(client)
    deadline_write_value, expected_deadline = build_deadline_roundtrip(
        metadata["date_deadline"]
    )
    todo_id = create_todo(project_task_model, arguments.todo_name_prefix)
    update_todo(project_task_model, todo_id, uid, deadline_write_value)
    verify_adapted_read(
        project_task_model,
        todo_id,
        uid,
        metadata["date_deadline"],
        expected_deadline,
    )

    if arguments.skip_auth_check:
        print_section("mapped auth error")
        print("SKIP: bad-password authentication check was skipped by request.")
    else:
        verify_authentication_error(settings)

    print_section("summary")
    print(f"Created ToDo id: {todo_id}")
    print(
        "The record was intentionally left in Odoo because delete/unlink is prohibited."
    )
    print_pass("Manual Phase B live smoke test completed.")
    return todo_id


def main() -> None:
    arguments = build_argument_parser().parse_args()
    created_todo_id: int | None = None

    try:
        created_todo_id = run_smoke(arguments)
    except Exception:
        if created_todo_id is not None:
            print(
                "\nNOTE: The smoke test created project.task "
                f"id {created_todo_id} before failing."
            )
        raise


if __name__ == "__main__":
    main()

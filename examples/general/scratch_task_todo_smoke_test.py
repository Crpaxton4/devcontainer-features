"""Human-readable smoke test for reading one task and creating one todo.

This example is intentionally small and explicit.

Why this file exists:
- The architecture docs move the SDK toward `OdooClient` -> `OdooEnv` ->
  recordset-oriented behavior.
- The current implementation now exposes a recordset-first public path directly,
    while `OdooModel` and `OdooQuery` remain compatibility shims.
- A smoke test should use the supported public path without hiding the remaining
    explicit extraction helpers.

Local configuration can come from either shell environment variables or a
supported INI file such as repository-root `.odoo_sdk.ini` with `url`, `db`,
`username`, and `password` under the `[odoo]` section.

Run with explicit task inputs:

    python examples/general/scratch_task_todo_smoke_test.py \
        --task-id 123 \
        --todo-name "Call customer back"
"""

from __future__ import annotations

import argparse
import os
from pprint import pprint
from typing import Any, Dict, Optional, Tuple

from odoo_sdk.odoo_service import OdooClient, OdooExecutor, OdooRecordset


TASK_FIELDS_TO_DISPLAY = [
    "id",
    "name",
    "project_id",
    "stage_id",
    "user_ids",
    "date_deadline",
    "description",
]

DEFAULT_TODO_NAME = "Smoke test todo created from the source checkout"


class RedPhaseSmokeExecutor(OdooExecutor):
    """Placeholder executor for source-checkout smoke runs.

    The goal of the fallback path is to keep local source-checkout runs explicit
    about missing live configuration instead of silently attempting real RPC calls.
    """

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "This smoke test requires a live Odoo configuration before it can "
            "exercise transport-backed recordset operations."
        )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read one existing task and create one todo using the current "
            "recordset-first surface."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--task-id",
        type=int,
        default=None,
        help=(
            "Existing project.task record to read before creating the todo. "
            "When omitted, the script reads the first visible task so local "
            "smoke runs do not stop at argument parsing."
        ),
    )
    parser.add_argument(
        "--todo-name",
        default=DEFAULT_TODO_NAME,
        help="Name for the new todo record.",
    )
    parser.add_argument(
        "--todo-description",
        default="",
        help="Optional description stored on the new todo.",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Read tasks with active_test disabled when you need archived rows.",
    )
    return parser


def build_client_from_local_configuration() -> Tuple[OdooClient, bool]:
    try:
        return OdooClient(), True
    except ValueError:
        return OdooClient(executor=RedPhaseSmokeExecutor()), False


def assert_recordset_first_surface(project_task_model: OdooRecordset) -> None:
    if not isinstance(project_task_model, OdooRecordset):
        raise AssertionError(
            "Recordset-first surface is expected: client['project.task'] should "
            "return OdooRecordset."
        )
    if project_task_model.ids != ():
        raise AssertionError(
            "Model-bound recordsets should start empty before search() or browse()."
        )


def read_single_task(
    project_task_model: OdooRecordset,
    task_id: Optional[int],
    *,
    include_inactive: bool,
) -> Dict[str, Any]:
    base_recordset = (
        project_task_model.with_context({"active_test": False})
        if include_inactive
        else project_task_model
    )

    if task_id is None:
        # A smoke check is more useful when it can discover one readable task on
        # its own instead of failing before it reaches the SDK surface.
        task_recordset = base_recordset.search([], limit=1)
    else:
        task_recordset = base_recordset.search([("id", "=", task_id)], limit=1)

    records = task_recordset.read(TASK_FIELDS_TO_DISPLAY)
    if not records:
        if task_id is None:
            raise LookupError("No readable task was found for the smoke test.")
        raise LookupError(f"No task was found for id {task_id}.")

    return records[0]


def create_todo_without_project(
    project_task_model: OdooRecordset,
    todo_name: str,
    todo_description: str,
) -> int:
    todo_values: Dict[str, Any] = {
        "name": todo_name,
        "project_id": False,
    }
    if todo_description:
        todo_values["description"] = todo_description

    # Setting project_id to False keeps this example honest about the intent:
    # we want a todo-shaped task, not a task that happens to inherit a project.
    return project_task_model.create(todo_values)


def main() -> None:
    arguments = build_argument_parser().parse_args()
    client, has_live_connection = build_client_from_local_configuration()

    project_task_model = client["project.task"]

    assert_recordset_first_surface(project_task_model)

    if not has_live_connection:
        raise SystemExit(
            "Provide ODOO_URL, ODOO_DB, ODOO_USERNAME, and ODOO_PASSWORD in the "
            "shell environment or a supported INI file once the "
            "recordset-first surface is ready and you want to exercise the live "
            "read/create flow."
        )

    existing_task = read_single_task(
        project_task_model,
        arguments.task_id,
        include_inactive=arguments.include_inactive,
    )
    print("Existing task:")
    pprint(existing_task)

    created_todo_id = create_todo_without_project(
        project_task_model,
        arguments.todo_name,
        arguments.todo_description,
    )
    print("\nCreated todo id:")
    print(created_todo_id)

    # Reading the new record back through the same surface gives one fast check
    # that both the write and the follow-up read are behaving as expected.
    created_todo = read_single_task(
        project_task_model,
        created_todo_id,
        include_inactive=True,
    )
    print("\nCreated todo:")
    pprint(created_todo)


if __name__ == "__main__":
    main()
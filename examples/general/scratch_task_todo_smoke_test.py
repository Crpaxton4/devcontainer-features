"""Human-readable smoke test for reading one task and creating one todo.

This example is intentionally small and explicit.

Why this file exists:
- The architecture docs move the SDK toward `OdooClient` -> `OdooEnv` ->
  recordset-oriented behavior.
- The current implementation still routes real work through the preserved
  `OdooModel` and `OdooQuery` compatibility surface.
- A smoke test should show the direction of travel without pretending that the
  recordset-first implementation is already finished.

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

from odoo_sdk.odoo_service import OdooClient, OdooExecutor, OdooModel, OdooQuery


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

    The goal of the red phase is to expose the missing public surface before a
    local example starts depending on live transport or credentials.
    """

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "This smoke test should fail on the missing recordset-first surface "
            "before it reaches live transport execution."
        )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read one existing task and create one todo using the current "
            "Phase A surface."
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


def assert_recordset_first_surface(project_task_model: OdooModel) -> None:
    search_result = project_task_model.search([])

    # The architecture docs treat recordsets as the future identity-bearing
    # result of search. As long as search still returns the preserved query
    # builder, the smoke test should stop there and report that gap explicitly.
    if isinstance(search_result, OdooQuery):
        raise NotImplementedError(
            "Recordset-first surface is not implemented yet: "
            "client.env['project.task'].search(...) still returns the preserved "
            "OdooQuery compatibility builder instead of a recordset-oriented "
            "result."
        )


def read_single_task(
    project_task_model: OdooModel,
    task_id: Optional[int],
    *,
    include_inactive: bool,
) -> Dict[str, Any]:
    if task_id is None:
        # A smoke check is more useful when it can discover one readable task on
        # its own instead of failing before it reaches the SDK surface.
        task_query = project_task_model.search([]).limit(1)
    else:
        task_query = project_task_model.search([("id", "=", task_id)]).limit(1)

    # The docs make env-bound context the long-term direction, but the current
    # runnable surface still applies context on the compatibility query object.
    if include_inactive:
        task_query = task_query.with_context({"active_test": False})

    records = task_query.read(TASK_FIELDS_TO_DISPLAY)
    if not records:
        if task_id is None:
            raise LookupError("No readable task was found for the smoke test.")
        raise LookupError(f"No task was found for id {task_id}.")

    return records[0]


def create_todo_without_project(
    project_task_model: OdooModel,
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

    # Starting from client.env mirrors the documented Phase A direction even
    # though the current implementation still delegates through model/query
    # compatibility objects underneath.
    project_task_model = client.env["project.task"]

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
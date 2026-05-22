"""Human-readable smoke test for reading one task and creating one todo.

This example is intentionally small and explicit.

Why this file exists:
- The architecture docs move the SDK toward `OdooClient` -> `OdooEnv` ->
  recordset-oriented behavior.
- The current implementation still routes real work through the preserved
  `OdooModel` and `OdooQuery` compatibility surface.
- A smoke test should show the direction of travel without pretending that the
  recordset-first implementation is already finished.

Run with connection settings in the environment and explicit task inputs:

    ODOO_URL=... ODOO_DB=... ODOO_USERNAME=... ODOO_PASSWORD=... \
    python examples/general/scratch_task_todo_smoke_test.py \
        --task-id 123 \
        --todo-name "Call customer back"
"""

from __future__ import annotations

import argparse
import os
from pprint import pprint
from typing import Any, Dict

from odoo_sdk.odoo_service import OdooClient, OdooModel


TASK_FIELDS_TO_DISPLAY = [
    "id",
    "name",
    "project_id",
    "stage_id",
    "user_ids",
    "date_deadline",
    "description",
]


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
        required=True,
        help="Existing project.task record to read before creating the todo.",
    )
    parser.add_argument(
        "--todo-name",
        required=True,
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


def build_client_from_environment() -> OdooClient:
    connection_values = {
        "ODOO_URL": os.getenv("ODOO_URL"),
        "ODOO_DB": os.getenv("ODOO_DB"),
        "ODOO_USERNAME": os.getenv("ODOO_USERNAME"),
        "ODOO_PASSWORD": os.getenv("ODOO_PASSWORD"),
    }
    missing_names = [
        variable_name
        for variable_name, variable_value in connection_values.items()
        if not variable_value
    ]
    if missing_names:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing_names)
        )

    return OdooClient(
        url=connection_values["ODOO_URL"],
        db=connection_values["ODOO_DB"],
        username=connection_values["ODOO_USERNAME"],
        password=connection_values["ODOO_PASSWORD"],
    )


def read_single_task(
    project_task_model: OdooModel,
    task_id: int,
    *,
    include_inactive: bool,
) -> Dict[str, Any]:
    task_query = project_task_model.search([("id", "=", task_id)]).limit(1)

    # The docs make env-bound context the long-term direction, but the current
    # runnable surface still applies context on the compatibility query object.
    if include_inactive:
        task_query = task_query.with_context({"active_test": False})

    records = task_query.read(TASK_FIELDS_TO_DISPLAY)
    if not records:
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
    client = build_client_from_environment()

    # Starting from client.env mirrors the documented Phase A direction even
    # though the current implementation still delegates through model/query
    # compatibility objects underneath.
    project_task_model = client.env["project.task"]

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
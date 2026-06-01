from typing import Any, Dict, Optional


class GetTodoCommand:
    """Returns one project task by id, or None if not found."""

    def __init__(self, client):
        self.client = client

    def __call__(self, task_id: int) -> Optional[Dict[str, Any]]:
        fields_to_fetch = [
            "name",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
        ]
        records = (
            self.client["project.task"]
            .search([("id", "=", task_id)], limit=1)
            .read(fields_to_fetch)
        )
        return records[0] if records else None


# Backward-compatible alias for previous class name.
GetToDoCommand = GetTodoCommand

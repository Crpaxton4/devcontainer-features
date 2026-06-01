from typing import Any, Dict, List, Optional, Tuple


class GetTasksCommand:
    """Lists project tasks with an optional domain filter."""

    def __init__(self, client):
        self.client = client

    def __call__(
        self, domain: Optional[List[Tuple[str, str, Any]]] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        task_domain = domain or []

        fields_to_fetch = [
            "name",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
        ]

        return (
            self.client["project.task"]
            .search(task_domain, limit=limit)
            .read(fields_to_fetch)
        )


# Backward-compatible alias for previous class name.
GetTaskCommand = GetTasksCommand

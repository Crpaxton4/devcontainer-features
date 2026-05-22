class CreateTaskCommand:
    """Creates a project task with standard default values."""

    def __init__(self, client):
        self.client = client

    def __call__(self, name: str, project_id: int, description: str = "") -> int:
        # Business logic: ensure tasks have a specific format or default values
        task_vals = {
            "name": f"[MCP] {name}",
            "project_id": project_id,
            "description": description,
        }
        return self.client["project.task"].create(task_vals)

from odoo_sdk.commands import Command


class CreateTaskCommand(Command):
    """Creates a project task with standard default values."""

    _name = "create_task"
    _description = "Creates a project task with standard default values."

    def execute(self, name: str, project_id: int, description: str = "") -> int:
        # Business logic: ensure tasks have a specific format or default values
        task_vals = {
            "name": f"[MCP] {name}",
            "project_id": project_id,
            "description": description,
        }
        return self._client["project.task"].create(task_vals)

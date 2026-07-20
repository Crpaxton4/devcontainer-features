from ..command import Command
from ._registration import builtin_command


@builtin_command
class CreateTaskCommand(Command):
    """Create a project task with a standard name prefix."""

    _name = "create_task"
    _description = "Creates a project task with standard default values."

    def execute(self, name: str, project_id: int, description: str = "") -> int:
        """Create a ``project.task`` (title prefixed ``[MCP]``) and return its id."""
        task_vals = {
            "name": f"[MCP] {name}",
            "project_id": project_id,
            "description": description,
        }
        return self._client["project.task"].create(task_vals)

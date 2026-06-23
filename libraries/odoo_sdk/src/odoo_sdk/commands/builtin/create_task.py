from ..command import Command


class CreateTaskCommand(Command):
    """Create a project task with a standard name prefix."""

    _name = "create_task"
    _description = "Creates a project task with standard default values."

    def execute(self, name: str, project_id: int, description: str = "") -> int:
        """Create a ``project.task`` and return its new id.

        :param name: Task title; prefixed with ``[MCP]`` on creation.
        :type name: str
        :param project_id: Identifier of the project the task belongs to.
        :type project_id: int
        :param description: Optional task description.
        :type description: str
        :return: The id of the created task.
        :rtype: int
        """

        task_vals = {
            "name": f"[MCP] {name}",
            "project_id": project_id,
            "description": description,
        }
        return self._client["project.task"].create(task_vals)

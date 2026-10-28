"""Create a project task with a standard name prefix."""

from dataclasses import dataclass

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    name: str
    project_id: int
    description: str = ""


@command(
    "create_task",
    args=Args,
    description="Creates a project task with standard default values.",
)
def handle(services: Services, args: Args) -> int:
    """Create a ``project.task`` (title prefixed ``[MCP]``) and return its id."""
    task_vals = {
        "name": f"[MCP] {args.name}",
        "project_id": args.project_id,
        "description": args.description,
    }
    return services.client["project.task"].create(task_vals)

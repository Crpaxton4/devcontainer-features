"""Pure builders for MCP prompt message strings.

These functions accept a plain task dict and return only strings; they perform no
I/O and have no side effects, so the business logic of building prompt context
lives in the utilities layer rather than inline in the prompt surface.
"""

from .odoo_helpers import format_chatter


def build_implement_task_messages(task: dict) -> list[str]:
    """Build the two-message ``implement_task`` prompt from task context.

    :param task: Task context dict (fields plus a ``chatter`` list).
    :type task: dict
    :return: ``[context_message, workflow_message]``.
    :rtype: list[str]
    """
    task_id = str(task.get("task_id", ""))
    name = task.get("name", "")
    project = task.get("project", "")
    stage = task.get("stage", "")
    assignees = ", ".join(str(x) for x in task.get("assignees") or []) or "—"
    deadline = task.get("deadline") or "—"
    priority = task.get("priority") or "—"
    tags = ", ".join(str(x) for x in task.get("tags") or []) or "—"
    description = task.get("description", "").strip() or "(no description)"
    chatter_text = format_chatter(task.get("chatter") or []) or "(no messages)"

    context_msg = (
        f"<task_context>\n"
        f"<task_id>{task_id}</task_id>\n"
        f"<name>{name}</name>\n"
        f"<project>{project}</project>\n"
        f"<stage>{stage}</stage>\n"
        f"<assignees>{assignees}</assignees>\n"
        f"<deadline>{deadline}</deadline>\n"
        f"<priority>{priority}</priority>\n"
        f"<tags>{tags}</tags>\n"
        f"<description>\n{description}\n</description>\n"
        f"<chatter>\n{chatter_text}\n</chatter>\n"
        f"</task_context>"
    )

    workflow_msg = (
        f"<workflow_instructions>\n"
        f"Follow these steps to implement this task:\n\n"
        f"1. **START** — Call `start_task` with:\n"
        f"   - `task_name_query=\"{name}\"`\n"
        f"   - `project_name_query=\"{project}\"`\n"
        f"   - `task_id={task_id}`\n\n"
        f"2. **ANALYZE** — Read `<description>` and `<chatter>` above. Identify what needs to be implemented.\n"
        f"   Post a plan note: `task_note({task_id}, \"Implementation plan: ...\")`\n\n"
        f"3. **IMPLEMENT** — Write the code.\n"
        f"   - Post progress: `task_note({task_id}, \"...\")`  at meaningful checkpoints.\n"
        f"   - If blocked: `task_question({task_id}, \"...\")`, then `resume_task({task_id})` when unblocked.\n\n"
        f"4. **STOP** — When done: `stop_task({task_id}, description=\"...\")` with a summary of changes.\n\n"
        f"## Tool Reference\n\n"
        f"| Tool | FSM Transition | When to Call |\n"
        f"|------|---------------|-------------|\n"
        f"| `start_task` | (absent) → RUNNING | Before writing any code |\n"
        f"| `task_note` | no state change | Progress checkpoints |\n"
        f"| `task_question` | RUNNING → AWAITING\\_ANSWERS | When blocked on clarification |\n"
        f"| `resume_task` | AWAITING\\_ANSWERS → RUNNING | After receiving answers |\n"
        f"| `stop_task` | active → STOPPED | Implementation complete |\n\n"
        f"## Guard Conditions\n\n"
        f"- `TaskAlreadyRunningError`: session already exists — call `task_status` first.\n"
        f"- `TaskNotRunningError`: no active session — ensure `start_task` succeeded.\n"
        f"- `InvalidStateTransitionError`: invalid transition — follow the table above.\n"
        f"</workflow_instructions>"
    )

    return [context_msg, workflow_msg]

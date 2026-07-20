"""MCP ``client_status_report`` prompt surface.

Ports the personal-features ``client-status-report`` skill (source of truth:
``devcontainer-features/src/personal-features/skills/client-status-report/SKILL.md``) to a
built-in MCP prompt, so any MCP client gets it without the mounted-SKILL.md
delivery path. The prompt takes no arguments and returns the skill's
instructional body verbatim for the caller to act on with its own (read-only)
Odoo tool calls; it never calls into the command registry itself.
"""

from odoo_sdk.commands import Registry

from ._registration import builtin_prompt

__all__ = ["make_client_status_report_prompt", "client_status_report"]

# Skill body embedded verbatim (frontmatter and the feature-managed HTML comment
# stripped) so the prompt ships with the SDK package rather than a mounted file.
_BODY = """\
# Weekly client status + billing report

Produce a client-ready status update backed entirely by Odoo data. Every hour
and every task in the report must come from a tool call — never from memory or
estimation. All tools below are read-only `odoo-sdk` MCP tools.

## 1. Pin down the reporting window and project

- Dates are **`YYYY-MM-DD`** and **inclusive on both ends**. A standard week is
  Monday→Sunday, e.g. `start_date="2026-07-06"`, `end_date="2026-07-12"`.
- If the report is for one client/project, resolve it first:
  `search_projects(query="<client name>")` → use the returned `project.project`
  id as `project_id` in the calls below. Omit `project_id` for an all-projects
  view.

## 2. Pull the hours (timesheet_summary)

`timesheet_summary(start_date, end_date, group_by=..., only_mine=True)` returns
`{group_by, start_date, end_date, only_mine, unit:"hours", groups:[{label,
hours, entries}], total_hours}`.

- `group_by` — pick the axis that answers the question:
  - `"project"` — hours per project (default; good for a multi-project week).
  - `"client"` — hours per customer (partner); use for a per-client roll-up.
  - `"task"` — hours per task; use inside a single project for line detail.
  - `"day"` — hours per calendar day; use to show cadence/pacing.
- `only_mine=True` (default) = the owner's own timesheets. Set `False` only when
  reporting a whole team's hours the user can see.
- Often run it twice: once `group_by="project"` (or `"client"`) for the roll-up,
  once `group_by="task"` for the line-item detail.

## 3. Pull unbilled hours (unbilled_hours)

`unbilled_hours(start_date, end_date, project_id)` → `{mode, count, total_hours,
lines[]}`; each line has `id, date, employee, project, task, hours, name`.

- Report `total_hours` as the unbilled figure and read the returned `mode`:
  - `"full"` — accurate; lines also carry `invoice_type` (billable vs not).
  - `"fallback"` — approximate ("not linked to a sale order line"); label it as
    an estimate in the report.
  - an error payload — the DB lacks the invoicing fields; say unbilled hours
    could not be determined rather than reporting zero.
- Leave dates unbounded only if you truly want lifetime unbilled; usually pass
  the same or a wider window than the status period.

## 4. Flag stale tasks (task_aging)

`task_aging(project_id=<id>, stage=<optional>, limit=20)` → open tasks sorted
stalest-first (longest since last activity). Use it to surface anything stuck.
Optionally filter by `stage` (e.g. a review/blocked column).

## 5. Optional: enrich blocked/at-risk items

For a task that needs a note on *why* it is stuck:
`get_task(task_id=<id>, include=["chatter"])` or
`get_task_chatter(task_id=<id>)` — quote the latest relevant update.

## Report structure

```
# Status report — <client / project> — week of <start_date> to <end_date>

## Summary
<2–3 sentences: overall progress and any headline risk>

## Done this week
- <task/deliverable completed>  (from timesheet_summary group_by="task" + task stage)

## In progress
- <task> — <short status>

## Blocked / needs client
- <task> — <blocker, quoting chatter> — <what you need from them>

## Hours
- Total logged: <total_hours> h  (timesheet_summary, <window>)
- By <project|client|day>: <label: h, …>

## Billing
- Unbilled hours: <total_hours> h  (mode: <full|fallback>; note if estimate)

## Stale / at-risk tasks
- <task> — <days since activity>  (task_aging)
```

## Do-not

- **Never invent, round-guess, or estimate hours.** Report exactly what
  `timesheet_summary` / `unbilled_hours` return; if a tool returns nothing, say
  "no hours logged", not a made-up number.
- Cite the window and the tool for every figure so it is auditable.
- Do not report unbilled hours as `0` when the tool returned an error/fallback —
  state the caveat and the `mode`.
- Do not include tasks the tools did not return; the report must be reproducible
  from the same calls.
- Keep the money commentary to hours and unbilled totals; rates, invoices, and
  what to bill are the owner's commercial call.
"""


def client_status_report() -> list[str]:
    """Generate a weekly client status and billing summary from Odoo timesheets. Use when the user asks for a weekly or periodic status report, a billing/hours summary, an unbilled-hours check, or wants stale tasks flagged for a client or project. Drives the read-only timesheet_summary, unbilled_hours, and task_aging Odoo tools."""
    return [_BODY]


@builtin_prompt("client_status_report")
def make_client_status_report_prompt(command_registry: Registry):
    """Register :func:`client_status_report` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`client_status_report` prompt callable, unchanged.
    """
    return client_status_report

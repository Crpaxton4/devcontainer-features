"""Built-in command merging duplicate timesheet entries (CLI ``normalize``)."""

from typing import Any

from ..command import Command
from ..protocols import RpcClient
from ._registration import builtin_command
from odoo_sdk.state import LocalStateClient
from odoo_sdk.utilities.timesheet import merge_timesheets


def _find_duplicate_timesheets(stopped: list) -> dict[tuple[int, str], list]:
    """Group stopped runs by task and calendar date, keeping only duplicates.

    :param stopped: Stopped runs that each carry a timesheet id.
    :type stopped: list
    :returns: Mapping of ``(task_id, day)`` to the runs sharing that key,
        limited to keys with more than one run.
    :rtype: dict[tuple[int, str], list]
    """
    groups: dict[tuple[int, str], list] = {}
    for run in stopped:
        day = run.started_at.date().isoformat()
        key = (run.task_id, day)
        groups.setdefault(key, []).append(run)
    return {key: runs for key, runs in groups.items() if len(runs) > 1}


def _apply_timesheet_merge(
    db: LocalStateClient,
    client: RpcClient,
    runs: list,
    ids: list[int],
) -> int:
    """Merge duplicate timesheet entries into the lowest id and remap runs.

    :param db: Local task-state database used to remap timesheet ids.
    :type db: LocalStateClient
    :param client: Odoo client used to merge the remote timesheets.
    :type client: RpcClient
    :param runs: Runs sharing the same task and calendar date.
    :type runs: list
    :param ids: Timesheet ids belonging to ``runs``.
    :type ids: list[int]
    :returns: The primary timesheet id the others were merged into.
    :rtype: int
    """
    primary = min(ids)
    others = [i for i in ids if i != primary]
    merge_timesheets(client, primary, others)
    for run in runs:
        if run.timesheet_id in others:
            db.remap_timesheet_id(run.timesheet_id, primary)
    return primary


@builtin_command
class NormalizeTimesheetsCommand(Command):
    """Detect (and optionally merge) duplicate timesheet entries.

    Two stopped runs of the same Odoo task on the same calendar date each carry
    their own ``account.analytic.line``; this command finds those duplicate
    groups. By default it only reports them (a dry run). With ``apply=True`` it
    merges each group's entries into their lowest timesheet id (via
    :func:`~odoo_sdk.utilities.timesheet.merge_timesheets`) and remaps the
    local runs onto that primary id, so the day's hours land on one timesheet.
    """

    _name = "normalize_timesheets"
    _description = (
        "Detect duplicate timesheet entries — stopped runs of the same task on "
        "the same calendar date — and, with apply=True, merge each group into "
        "its lowest timesheet id (remapping the local runs). apply=False (the "
        "default) only reports the duplicates."
    )

    def execute(self, apply: bool = False) -> dict[str, Any]:
        """Report duplicate timesheet groups, merging them when ``apply``.

        :param apply: When ``True``, merge each duplicate group; otherwise only
            report (a dry run).
        :type apply: bool
        :returns: ``{"applied": bool, "groups": [...]}`` where each group is
            ``{"task_id", "task_name", "day", "count", "total_hours",
            "timesheet_ids", "merged_into"}``. ``merged_into`` is the primary
            timesheet id when the group was merged, else ``None``.
        :rtype: dict[str, Any]
        """
        db = self.state
        duplicates = _find_duplicate_timesheets(db.get_stopped_runs_with_timesheet())
        groups: list[dict[str, Any]] = []
        for (task_id, day), runs in duplicates.items():
            ids = [run.timesheet_id for run in runs if run.timesheet_id]
            merged_into = None
            if apply and len(ids) > 1:
                merged_into = _apply_timesheet_merge(db, self._client, runs, ids)
            groups.append(
                {
                    "task_id": task_id,
                    "task_name": runs[0].task_name,
                    "day": day,
                    "count": len(ids),
                    "total_hours": sum(run.elapsed_hours for run in runs),
                    "timesheet_ids": ids,
                    "merged_into": merged_into,
                }
            )
        return {"applied": apply, "groups": groups}

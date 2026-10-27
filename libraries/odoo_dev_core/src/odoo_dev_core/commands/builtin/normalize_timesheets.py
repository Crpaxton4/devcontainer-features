"""Built-in command merging duplicate timesheet entries (CLI ``normalize``)."""

from datetime import datetime
from typing import Any

from ..command import Command
from ..protocols import RpcClient, StateStore
from ._registration import builtin_command
from odoo_dev_core.billing.timesheet import merge_timesheets


def _find_duplicate_timesheets(uploads: list[dict]) -> dict[tuple[str, str], list]:
    """Group session uploads by task and calendar date, keeping only duplicates.

    Legacy upload rows without a ``task_id`` or ``started_at`` cannot be grouped
    and are skipped. Uploads that share one timesheet id are already merged and
    count once.

    :param uploads: Rows from :meth:`StateStore.list_session_uploads`.
    :type uploads: list[dict]
    :returns: Mapping of ``(task_id, day)`` to the uploads sharing that key,
        limited to keys that hold more than one distinct timesheet id.
    :rtype: dict[tuple[str, str], list]
    """
    groups: dict[tuple[str, str], list] = {}
    for upload in uploads:
        if not upload["task_id"] or not upload["started_at"]:
            continue
        day = upload["started_at"][:10]
        groups.setdefault((upload["task_id"], day), []).append(upload)
    return {
        key: rows
        for key, rows in groups.items()
        if len({row["timesheet_id"] for row in rows}) > 1
    }


def _apply_timesheet_merge(
    db: StateStore,
    client: RpcClient,
    uploads: list[dict],
    ids: list[int],
) -> int:
    """Merge duplicate timesheet entries into the lowest id and remap uploads.

    :param db: Local state store used to remap the upload ledger.
    :type db: StateStore
    :param client: Odoo client used to merge the remote timesheets.
    :type client: RpcClient
    :param uploads: Session uploads sharing the same task and calendar date.
    :type uploads: list[dict]
    :param ids: Distinct timesheet ids belonging to ``uploads``.
    :type ids: list[int]
    :returns: The primary timesheet id the others were merged into.
    :rtype: int
    """
    primary = min(ids)
    others = [i for i in ids if i != primary]
    merge_timesheets(client, primary, others)
    for upload in uploads:
        if upload["timesheet_id"] in others:
            db.record_session_upload(
                upload["session_key"],
                primary,
                upload["hours"],
                task_id=upload["task_id"],
                started_at=datetime.fromisoformat(upload["started_at"]),
                ended_at=(
                    datetime.fromisoformat(upload["ended_at"])
                    if upload["ended_at"]
                    else None
                ),
            )
    return primary


@builtin_command
class NormalizeTimesheetsCommand(Command):
    """Detect (and optionally merge) duplicate timesheet entries.

    Two uploaded sessions of the same Odoo task on the same calendar date each
    carry their own ``account.analytic.line``; this command finds those
    duplicate groups. By default it only reports them (a dry run). With
    ``apply=True`` it merges each group's entries into their lowest timesheet id
    (via :func:`~odoo_dev_core.billing.timesheet.merge_timesheets`) and remaps the
    upload ledger onto that primary id, so the day's hours land on one timesheet.
    """

    _name = "normalize_timesheets"
    _description = (
        "Detect duplicate timesheet entries — uploaded sessions of the same "
        "task on the same calendar date — and, with apply=True, merge each "
        "group into its lowest timesheet id (remapping the upload ledger). "
        "apply=False (the default) only reports the duplicates."
    )

    def execute(self, apply: bool = False) -> dict[str, Any]:
        """Report duplicate timesheet groups, merging them when ``apply``.

        :param apply: When ``True``, merge each duplicate group; otherwise only
            report (a dry run).
        :type apply: bool
        :returns: ``{"applied": bool, "groups": [...]}`` where each group is
            ``{"task_id", "day", "count", "total_hours", "timesheet_ids",
            "merged_into"}``. ``merged_into`` is the primary timesheet id when
            the group was merged, else ``None``.
        :rtype: dict[str, Any]
        """
        db = self.state
        duplicates = _find_duplicate_timesheets(db.list_session_uploads())
        groups: list[dict[str, Any]] = []
        for (task_id, day), uploads in duplicates.items():
            ids = sorted({row["timesheet_id"] for row in uploads})
            merged_into = None
            if apply:
                merged_into = _apply_timesheet_merge(db, self._client, uploads, ids)
            groups.append(
                {
                    "task_id": task_id,
                    "day": day,
                    "count": len(ids),
                    "total_hours": sum(row["hours"] for row in uploads),
                    "timesheet_ids": ids,
                    "merged_into": merged_into,
                }
            )
        return {"applied": apply, "groups": groups}

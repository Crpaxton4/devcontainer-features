"""Shared builders for sessionization tests."""

from datetime import date, datetime, timezone

from odoo_sdk.sessionization import EventType, RawEvent, SessionizationConfig

UTC = timezone.utc


def raw_event(
    hour: int,
    minute: int,
    task: str = "101",
    event_type: EventType = EventType.COMMIT,
    repo: str = "owner/repo",
    pr_num: int = 0,
    branch: str = "",
    day: int = 1,
) -> RawEvent:
    """Build a tz-aware :class:`RawEvent` on 2026-06-``day``."""
    return RawEvent(
        timestamp=datetime(2026, 6, day, hour, minute, tzinfo=UTC),
        task_ids=[task] if task else [],
        repo=repo,
        pr_num=pr_num,
        event_type=event_type,
        branch=branch,
        subject=f"subject {task}",
        pr_title=f"PR title {task}",
        pr_body=f"PR body {task}",
    )


def one_day_config(**overrides) -> SessionizationConfig:
    """Return a single-day config over 2026-06-01 with optional overrides."""
    values = {"start_date": date(2026, 6, 1), "end_date": date(2026, 6, 1)}
    values.update(overrides)
    return SessionizationConfig(**values)

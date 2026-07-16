"""Pure markdown diagnostics rendering for the sessionization ETL.

Every function returns a list of lines (or a full string); nothing is written to
disk. ``render_markdown`` assembles the sweep tables, summary, audit warnings,
final entry table, a Mermaid Gantt chart, and the unresolved-source audit into a
single markdown document.
"""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime, timedelta

from .config import SessionizationConfig
from .formatting import (
    fmt_delta,
    fmt_duration,
    fmt_et,
    is_numeric_id,
    md_table_header,
)
from .models import ET, RawEvent, SweepResults, TimeEntry, TransformResult
from .scoring import score_gap
from .transform import target_day_totals


def _final_table(entries: list[TimeEntry]) -> list[str]:
    """Return the chronological final time-entry table lines."""
    lines = ["", "## Final Time Entries", ""]
    cols = [("REPO", 38), ("TASK", 5), ("START", 20), ("END", 20), ("DURATION", 8)]
    lines.extend(md_table_header(cols))
    total_secs = 0
    for entry in sorted(entries, key=lambda x: (x.repo, x.start)):
        dur = entry.duration_secs
        total_secs += dur
        lines.append(
            f"| {entry.repo:<38} | {entry.task_id:<5} | {fmt_et(entry.start):<20} | "
            f"{fmt_et(entry.end):<20} | {fmt_duration(dur):<8} |"
        )
    total = "**" + fmt_duration(total_secs) + "**"
    lines.append(f"| {'**TOTAL**':<38} | {'':<5} | {'':<20} | {'':<20} | {total:<8} |")
    return lines


def _rle_sweep_rows(
    totals: list[float],
    gap_vals: list[int],
    num_days: int,
    config: SessionizationConfig,
) -> list[str]:
    """Run-length encode identical sweep totals into table row strings."""
    rows: list[str] = []
    for run_val, group in itertools.groupby(range(len(totals)), key=totals.__getitem__):
        idx = list(group)
        first, last = idx[0], idx[-1]
        gap_range = (
            f"{gap_vals[first]}m"
            if first == last
            else f"{gap_vals[first]}-{gap_vals[last]}m"
        )
        s = score_gap(run_val, num_days, config)
        rows.append(
            f"| {gap_range:<12} | {s:<8.3f} | "
            f"{fmt_duration(int(run_val)):<12} | {len(idx):<8} |"
        )
    return rows


def _sweep_tables(results: SweepResults, config: SessionizationConfig) -> list[str]:
    """Return per-task sweep tables with run-length-encoded totals."""
    step = (
        results.gap_vals[1] - results.gap_vals[0]
        if len(results.gap_vals) > 1
        else 0
    )
    lines = [
        "",
        f"## Sweep ({results.gap_vals[0]}-{results.gap_vals[-1]} min, {step}-min steps)",
    ]
    cols = [("GAP RANGE", 12), ("SCORE", 8), ("TOTAL", 12), ("N_GAPS", 8)]
    for task_id in sorted(results.per_task):
        lines.extend(["", f"### Task {task_id}", ""])
        lines.extend(md_table_header(cols))
        lines.extend(
            _rle_sweep_rows(
                results.per_task[task_id],
                results.gap_vals,
                results.num_days,
                config,
            )
        )
    return lines


def _fmt_excluded_target_dates(config: SessionizationConfig) -> str:
    """Return excluded-target-date text for report summaries."""
    excluded = sorted(
        day
        for day in config.target_excluded_dates
        if config.start_date <= day <= config.end_date
    )
    if not excluded:
        return ""
    dates = ", ".join(day.isoformat() for day in excluded)
    return f", excludes {dates}"


def _sweep_summary(results: SweepResults, config: SessionizationConfig) -> list[str]:
    """Return the sweep summary metrics table lines."""
    target_secs = config.b_low * 3600 * config.num_days
    best_dist = results.best_total - target_secs
    excluded = _fmt_excluded_target_dates(config)
    lines = ["", "## Sweep Summary", ""]
    lines.extend(md_table_header([("METRIC", 14), ("VALUE", 30)]))
    lines.append(f"| {'obs mean':<14} | {fmt_duration(int(results.obs_mean)):<30} |")
    lines.append(
        f"| {'target':<14} | {fmt_duration(int(target_secs))} "
        f"({config.b_low}h/day x {config.num_days}d{excluded})  |"
    )
    lines.append(f"| {'best gap':<14} | {results.best_gap}m{'':<24} |")
    lines.append(f"| {'best total':<14} | {fmt_duration(int(results.best_total)):<30} |")
    lines.append(f"| {'best score':<14} | {results.best_score:<30.4f} |")
    lines.append(f"| {'best delta':<14} | {fmt_delta(best_dist):<30} |")
    return lines


def _audit_warning_rows(
    result: TransformResult, config: SessionizationConfig
) -> list[tuple[str, str]]:
    """Build audit warning rows for suspicious generated billing output."""
    rows: list[tuple[str, str]] = []
    upper_secs = config.b_high * 3600
    over_days = {
        day: total
        for day, total in target_day_totals(result.best_gap_entries, config).items()
        if total > upper_secs
    }
    if over_days:
        details = ", ".join(
            f"{day.isoformat()} {fmt_duration(int(total))}"
            for day, total in sorted(over_days.items())
        )
        rows.append(
            (
                "over upper bound",
                f"{details} > {fmt_duration(int(upper_secs))}/day "
                f"({config.b_high}h/day)",
            )
        )
    empty = sum(
        1 for entry in result.best_gap_entries if not is_numeric_id(entry.task_id)
    )
    if empty:
        rows.append(("empty CSV Task/ID rows", str(empty)))
    return rows


def _audit_warnings(result: TransformResult, config: SessionizationConfig) -> list[str]:
    """Return audit-warning table lines, or empty if none apply."""
    rows = _audit_warning_rows(result, config)
    if not rows:
        return []
    lines = ["", "## Audit Warnings", ""]
    lines.extend(md_table_header([("WARNING", 24), ("DETAIL", 56)]))
    for warning, detail in rows:
        lines.append(f"| {warning:<24} | {detail:<56} |")
    return lines


def _unknown_events(events: list[RawEvent]) -> list[RawEvent]:
    """Return unresolved source events, sorted for the audit table."""
    return sorted(
        (
            event
            for event in events
            if not event.task_ids or "UNKNOWN" in event.task_ids
        ),
        key=lambda event: (event.timestamp, event.repo, event.pr_num, event.branch),
    )


def _unknown_sources(result: TransformResult) -> list[str]:
    """Return the unresolved-source audit table lines."""
    unknown = _unknown_events(result.raw_events)
    lines = ["", "## Unresolved Task Sources", ""]
    if not unknown:
        lines.append("No UNKNOWN source events were found.")
        return lines
    cols = [
        ("EVENT TIME", 20),
        ("REPO", 32),
        ("SOURCE", 14),
        ("BRANCH", 28),
        ("EVENT", 8),
        ("STATUS", 30),
    ]
    lines.extend(md_table_header(cols))
    for event in unknown:
        source = f"PR #{event.pr_num}" if event.pr_num else "local git"
        branch = event.branch or "(empty)"
        lines.append(
            f"| {fmt_et(event.timestamp):<20} | {event.repo:<32} | "
            f"{source:<14} | {branch:<28} | "
            f"{event.event_type.name.lower():<8} | excluded from billing output   |"
        )
    return lines


def _mermaid_text(text: str) -> str:
    """Sanitize display text for Mermaid Gantt labels and sections."""
    text = text.replace(":", " ").replace(",", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip() or "entry"


def _mermaid_datetime(ts: datetime) -> str:
    """Format a timestamp for Mermaid Gantt using local day-bucket clock time."""
    return ts.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _entries_by_task(entries: list[TimeEntry]) -> dict[str, list[TimeEntry]]:
    """Group entries by task id."""
    by_task: dict[str, list[TimeEntry]] = {}
    for entry in entries:
        by_task.setdefault(entry.task_id, []).append(entry)
    return by_task


def _mermaid_gantt(title: str, entries: list[TimeEntry]) -> list[str]:
    """Return a Mermaid Gantt chart for entry windows grouped by task."""
    lines = [
        "",
        f"## {title}",
        "",
        "```mermaid",
        "gantt",
        f"    title {_mermaid_text(title)} (local)",
        "    dateFormat YYYY-MM-DD HH:mm",
        "    axisFormat %m-%d %H:%M",
        "    tickInterval 1day",
    ]
    entry_num = 1
    for task_id, task_entries in sorted(_entries_by_task(entries).items()):
        lines.append(f"    section Task {_mermaid_text(task_id)}")
        ordered = sorted(task_entries, key=lambda e: (e.start, e.repo, e.pr_num))
        for entry in ordered:
            entry_id = f"entry_{entry_num:04d}"
            lines.append(
                f"    . :{entry_id}, "
                f"{_mermaid_datetime(entry.start)}, {_mermaid_datetime(entry.end)}"
            )
            entry_num += 1
    lines.append("```")
    return lines


def render_markdown(result: TransformResult, config: SessionizationConfig) -> str:
    """Assemble the full markdown diagnostics document as a string."""
    lines: list[str] = []
    lines.extend(_sweep_tables(result.sweep, config))
    lines.extend(_sweep_summary(result.sweep, config))
    lines.extend(_audit_warnings(result, config))
    lines.extend(_final_table(result.best_gap_entries))
    lines.extend(
        _mermaid_gantt(
            f"Final Window Diagram (gap = {result.sweep.best_gap}m)",
            result.best_gap_entries,
        )
    )
    lines.extend(_unknown_sources(result))
    lines.append("")
    return "\n".join(lines) + "\n"

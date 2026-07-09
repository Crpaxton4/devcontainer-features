#!/usr/bin/env python3
"""timelog.py — Reconstruct billable time from GitHub activity.

Architecture: ETL
  Extract   — fetch RawEvents from GitHub API + local git repos
  Transform — compute time windows, sweep gap values, score gaps
  Load      — write the final Markdown report

Diagnostics are side-effects emitted inside Extract/Transform via
Config.diag (DiagnosticWriter).  main() is output-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import csv
import io
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import IO

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — edit these globals to change runtime behaviour
# ══════════════════════════════════════════════════════════════════════════════

GH_USER = "cpqoc"
ORG = ""  # restrict search to this org owner (or "")
START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 2)
TARGET_EXCLUDED_DATES: set[date] = set()
OUTFILE = "timelog.md"

WINDOW_GAP_SECS = 3600  # default gap used for the main Table section
MIN_TASK_MINUTES = 15  # absolute minimum billable unit (floor)
BILLING_STEP_MINS = 15  # round all durations UP to the nearest multiple of this

SWEEP_MIN_GAP_MINS = 30  # must be >= 2 * MIN_TASK_MINUTES to guarantee monotonicity
SWEEP_MAX_GAP_MINS = 360
SWEEP_STEP_MINS = 5

# Path scanned for local .git repos (each subdirectory with .git is included).
# Set to "" to disable local scanning.  Tilde is expanded at runtime.
LOCAL_REPOS_PATH = "~/repos"
LOCAL_EVENTS_CACHE_FILE = ".timelog_local_events_cache.json"
TUNING_CONFIG_FILE = "timelog_tuning_config.json"

# ── Odoo CSV export ──────────────────────────────────────────────────────────
# Set ODOO_CSV_FILE = "" to disable CSV output.
ODOO_CSV_FILE = "timelog_odoo.csv"
ODOO_EMPLOYEE_ID = 49  # hr.employee ID for the current user
ODOO_UOM_ID = 6  # uom.uom ID for Hours
ODOO_COMPANY_ID = 1  # res.company ID

# ── AI CSV description export ───────────────────────────────────────────────
DEFAULT_DESCRIPTION = "Development work"
AI_DESCRIPTION_PREFIX = "[/]"
CLAUDE_DESCRIPTION_TIMEOUT_SECS = 40
CLAUDE_DESCRIPTION_CONCURRENCY = 8

# ── Utilisation scoring ───────────────────────────────────────────────────────
# The sweep picks the gap whose avg h/day maximises f(x) (see score_gap).
#
#   x < B_LOW  : underutilisation  — exp penalty toward −∞ (rate K1)
#   B_LOW…HIGH : optimal zone      — exp growth from S_LOW to S_HIGH (rate K2)
#   x > B_HIGH : dishonesty        — exp penalty toward −∞ (rate K3)
#
# f(B_LOW) = S_LOW and f(B_HIGH) = S_HIGH exactly (C0 continuity guaranteed).
B_LOW = 8.0  # h/day — lower utilisation boundary (min acceptable)
B_HIGH = 10.0  # h/day — upper boundary (implausibly high = dishonest)
S_LOW = 0.0  # anchor score at B_LOW  (must be strictly < S_HIGH)
S_HIGH = 1.0  # anchor score at B_HIGH
K1 = 0.5  # underutilisation exp rate
K2 = 1.0  # optimal-zone growth ratebl
K3 = 2.0  # dishonesty exp rate (independent of K1)

# ══════════════════════════════════════════════════════════════════════════════
# § 1  DATA MODEL
# ══════════════════════════════════════════════════════════════════════════════

ET = timezone(timedelta(hours=-4), "ET")  # EDT (UTC-4); adjust to -5 in winter


class EventType(Enum):
    COMMIT = auto()
    MERGE = auto()
    REVIEW = auto()


@dataclass
class RawEvent:
    """Normalised event from any source."""

    timestamp: datetime  # tz-aware UTC
    task_ids: list[str]  # extracted task IDs (may be empty → ["UNKNOWN"])
    repo: str  # "owner/repo"
    pr_num: int  # 0 for local-git commits with no PR
    event_type: EventType
    branch: str = ""
    is_release: bool = False  # True when len(task_ids) > 1
    subject: str = ""
    pr_title: str = ""
    pr_body: str = ""


@dataclass
class TimeEntry:
    """A computed, bounded time block attributed to one task."""

    task_id: str
    repo: str
    pr_num: int
    start: datetime  # tz-aware UTC
    end: datetime  # tz-aware UTC
    label: str = ""  # "owner/repo#num"
    branch: str = ""
    source_events: list[RawEvent] = field(default_factory=list)
    strategy_name: str = "development"
    strategy_category: str = "Development"
    activity_type: str = ""


@dataclass(frozen=True)
class SessionStrategyConfig:
    """Flat configuration row for one sessionization strategy."""

    name: str
    category: str
    event_types: tuple[EventType, ...]
    strategy_kind: str
    group_keys: tuple[str, ...]
    gap_secs: int = WINDOW_GAP_SECS
    fixed_secs: int = MIN_TASK_MINUTES * 60
    billing_floor_secs: int = MIN_TASK_MINUTES * 60
    billing_step_secs: int = BILLING_STEP_MINS * 60
    sweep_enabled: bool = False
    context_fields: tuple[str, ...] = ("pr_title", "subject", "pr_body")
    context_limit: int = 220
    fallback_action: str = "advanced project work"
    priority: int = 100


@dataclass
class SweepResults:
    gap_vals: list[int]  # gap values tested (minutes)
    combined: list[float]  # total secs at each gap
    per_task: dict[str, list[float]]  # task_id → secs at each gap
    scores: list[float]  # average per-target-day score at each gap
    obs_mean: float
    best_gap: int  # minutes
    best_total: float  # secs
    best_score: float
    num_days: int


@dataclass
class TransformResult:
    """Complete, fully-computed output of the Transform phase.

    load() accepts only this object — it calls no Transform functions.
    """

    all_entries: list[TimeEntry]  # entries at default gap  → Table section
    best_gap_entries: list[TimeEntry]  # entries at best gap     → Odoo section
    sweep: SweepResults
    raw_events: list[RawEvent] = field(default_factory=list)


@dataclass
class PRDiagnostics:
    """Diagnostic payload returned by PR processors.

    Carries all data needed to write diagnostic lines.
    No I/O — processors build this; phase functions write it.
    """

    repo: str
    num: int
    branch: str
    body: str
    tasks: list[str]
    outcome: str  # "authored"|"release"|"reviewed"|"skip_overlap"|"skip_no_activity"
    created_at: datetime | None = None
    closed_at: datetime | None = None
    # Each entry: (ts, kind, in_range) — kind is "commit"|"lifecycle"|"review"
    timestamps: list[tuple] = field(default_factory=list)
    fetched_count: int = 0
    in_range_count: int = 0
    lifecycle_in_range_count: int = 0
    # Each entry: (event_kind, ts, num_tasks) — "merge" or "review"
    source_events: list[tuple] = field(default_factory=list)


@dataclass
class LocalCommitDiag:
    """Diagnostic payload for one local-git commit."""

    repo_dir: str
    ts: datetime
    branch: str
    tasks: list[str]


@dataclass
class Config:
    # ── Identity ──────────────────────────────────────────────────────────────
    gh_user: str = "cpqoc"
    org: str = ""

    # ── Date range ───────────────────────────────────────────────────────────
    start_date: date = field(default_factory=lambda: date(2026, 6, 1))
    end_date: date = field(default_factory=lambda: date(2026, 6, 2))
    target_excluded_dates: set[date] = field(default_factory=set)

    # ── Window / billing ─────────────────────────────────────────────────────
    window_gap_secs: int = 3600  # default gap for initial window calc
    min_task_minutes: int = 15  # absolute minimum billable unit
    billing_step_mins: int = 15  # round all durations UP to this step (minutes)

    # ── Sweep ─────────────────────────────────────────────────────────────────
    sweep_min_gap_mins: int = 30
    sweep_max_gap_mins: int = 240
    sweep_step_mins: int = 5

    # ── Utilisation scoring (fed from CONFIG globals) ─────────────────────────
    b_low: float = 8.0  # lower utilisation boundary h/day
    b_high: float = 12.0  # upper (dishonesty) boundary h/day
    s_low: float = 0.0  # anchor score at b_low
    s_high: float = 1.0  # anchor score at b_high
    k1: float = 0.5  # underutilisation exp rate
    k2: float = 1.0  # optimal-zone growth rate
    k3: float = 2.0  # dishonesty exp rate

    # ── Output ───────────────────────────────────────────────────────────────
    outfile: str = "timelog.md"
    local_repos_path: str = "~/repos"  # path scanned for .git dirs; "" to disable
    local_events_cache_file: str = ".timelog_local_events_cache.json"
    odoo_csv_file: str = "timelog_odoo.csv"  # set "" to disable
    odoo_employee_id: int = 49
    odoo_uom_id: int = 6
    odoo_company_id: int = 1

    # ── Computed / runtime ───────────────────────────────────────────────────
    diag: "DiagnosticWriter | None" = field(default=None, repr=False)
    session_strategy_configs: tuple[SessionStrategyConfig, ...] = field(
        default_factory=lambda: DEFAULT_SESSION_STRATEGY_CONFIGS
    )

    def __post_init__(self) -> None:
        """Validate sweep_min_gap_mins >= 2 * min_task_minutes.

        This constraint guarantees that total billed time is monotonically
        non-decreasing as gap grows with the max(elapsed, floor) formula:
        any two sessions that merge are >= 2*floor apart, so merged elapsed
        always exceeds the sum of the two floored originals.
        """
        min_valid = 2 * self.min_task_minutes
        if self.sweep_min_gap_mins < min_valid:
            raise ValueError(
                "sweep_min_gap_mins must be at least "
                f"2 * min_task_minutes ({min_valid})"
            )

    # ── Derived helpers ───────────────────────────────────────────────────────
    @property
    def range_start(self) -> datetime:
        """Midnight ET on start_date (tz-aware)."""
        return datetime(
            self.start_date.year, self.start_date.month, self.start_date.day, tzinfo=ET
        )

    @property
    def range_end(self) -> datetime:
        """Midnight ET on day *after* end_date (half-open interval)."""
        next_day = self.end_date + timedelta(days=1)
        return datetime(next_day.year, next_day.month, next_day.day, tzinfo=ET)

    @property
    def num_days(self) -> int:
        return len(self.target_dates)

    @property
    def target_dates(self) -> list[date]:
        days = (self.end_date - self.start_date).days + 1
        return [
            self.start_date + timedelta(days=offset)
            for offset in range(days)
            if self.start_date + timedelta(days=offset)
            not in self.target_excluded_dates
        ]

    @property
    def min_task_secs(self) -> int:
        return self.min_task_minutes * 60

    def in_range(self, ts: datetime) -> bool:
        """True iff ts falls in [range_start, range_end)."""
        return self.range_start <= ts < self.range_end


# ══════════════════════════════════════════════════════════════════════════════
# § 2  DIAGNOSTICS  (side-effect only — never called from main/ETL orchestration)
# ══════════════════════════════════════════════════════════════════════════════


class DiagnosticWriter:
    """Incrementally writes the diagnostic fenced-code block to outfile.

    Stored on Config so ETL functions can call it without threading it
    through every signature.  main() is completely unaware of this object
    except for the one-time open/close calls.
    """

    def __init__(self, outfile: str, start_date: date, end_date: date) -> None:
        self._f: IO[str] = open(outfile, "w", encoding="utf-8")
        self._write(f"# Time Log: {start_date} – {end_date}\n\n## Diagnostics\n\n```\n")

    def close_code_block(self) -> None:
        self._write("```\n")

    def close(self) -> None:
        self._f.close()

    def github(self, repo: str, num: int) -> None:
        self._write(f"\n---\n\nrepository\t{repo}\nPR\t#{num}\n")

    def authored(self, repo: str, num: int, branch: str, task: str) -> None:
        self._write(f"authored\t{repo}#{num}\tbranch={branch}\ttask={task}\n")

    def release(self, repo: str, num: int, branch: str, tasks: list[str]) -> None:
        self._write(
            f"release\t{repo}#{num}\tbranch={branch}\ttasks={','.join(tasks)}\n"
        )

    def reviewed(self, repo: str, num: int, branch: str, task: str) -> None:
        self._write(f"reviewed\t{repo}#{num}\tbranch={branch}\ttask={task}\n")

    def ts(self, marker: str, ts: datetime, kind: str = "commit") -> None:
        """Log a discovered timestamp.

        kind: "commit" | "lifecycle" | "review"
        Lifecycle timestamps (createdAt, closedAt) are shown but never billed.
        """
        self._write(f"  ts\t{marker}\t[{kind}]\t{ts.isoformat()}\n")

    def window(self, start: datetime, end: datetime, note: str = "") -> None:
        dur = _fmt_duration(int((end - start).total_seconds()))
        note_part = f"\t{note}" if note else ""
        self._write(f"  window\t{_fmt_et(start)}\t{_fmt_et(end)}\t{dur}{note_part}\n")

    def source_event(self, event_kind: str, event_at: datetime, num_tasks: int) -> None:
        self._write(
            f"  event\t[{event_kind}]\t{event_at.isoformat()}\t{num_tasks}task\n"
        )

    def task_extract(self, branch: str, body: str, tasks: list[str]) -> None:
        """Log how task IDs were resolved for a PR."""
        sources: list[str] = []
        if re.match(r"^\d+", branch):
            sources.append(f"branch-prefix={branch.split('#')[0]}")
        body_ids = re.findall(r"/web#[^\s\"<>]*?id=(\d+)", body)
        if body_ids:
            sources.append(f"body-links={len(body_ids)}")
        task_links = re.findall(r"/odoo/(?:my-tasks|[^/\s]+/tasks)/(\d+)", body)
        if task_links:
            sources.append(f"odoo-links={len(task_links)}")
        hd_links = re.findall(r"/odoo/helpdesk\.ticket/(\d+)", body)
        if hd_links:
            sources.append(f"helpdesk-links={len(hd_links)}")
        src_str = ",".join(sources) if sources else "none"
        self._write(f"  task_extract\ttasks={','.join(tasks)}\tsrc={src_str}\n")

    def commit_summary(
        self, fetched: int, in_range_commits: int, lifecycle_in_range: int
    ) -> None:
        """Log commit/review counts after API fetch and range filtering."""
        billed = "yes" if in_range_commits > 0 else "no"
        self._write(
            f"  commit_summary\tfetched={fetched}\tin_range={in_range_commits}"
            f"\tlifecycle_in_range={lifecycle_in_range}\tbillable={billed}\n"
        )

    def skip_no_activity(self, repo: str, num: int, branch: str) -> None:
        """Log a PR skipped after API fetch found no in-range activity."""
        self._write(f"skip_no_activity\t{repo}#{num}\tbranch={branch}\n")

    def skip_overlap(
        self,
        repo: str,
        num: int,
        pr_start: "datetime | None",
        pr_end: "datetime | None",
    ) -> None:
        """Log a PR skipped before API fetch — lifetime outside target range."""
        s = pr_start.strftime("%Y-%m-%d") if pr_start else "?"
        e = pr_end.strftime("%Y-%m-%d") if pr_end else "?"
        self._write(f"skip_overlap\t{repo}#{num}\tpr_range={s}..{e}\n")

    def local_commit(
        self, repo_dir: str, ts: datetime, branch: str, tasks: list[str]
    ) -> None:
        """Log a commit discovered from a local .git repository."""
        self._write(
            f"  local_commit\t{repo_dir}\tbranch={branch}"
            f"\ttasks={','.join(tasks)}\t{ts.isoformat()}\n"
        )

    def dedup_skip(
        self, ts: datetime, repo: str, tasks: list[str], kept_pr_num: int
    ) -> None:
        """Log a COMMIT event dropped because an identical timestamp already exists."""
        self._write(
            f"  dedup_skip\t{repo}\ttasks={','.join(tasks)}"
            f"\tkept_pr=#{kept_pr_num}\t{ts.isoformat()}\n"
        )

    def _write(self, text: str) -> None:
        self._f.write(text)
        self._f.flush()


# ══════════════════════════════════════════════════════════════════════════════
# § 3  FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _fmt_et(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def _fmt_duration(secs: int) -> str:
    secs = max(0, secs)
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _fmt_delta(secs: float) -> str:
    secs = int(secs)
    if secs == 0:
        return "0h 0m"
    sign = "+" if secs > 0 else "−"
    secs = abs(secs)
    return f"{sign}{secs // 3600}h {(secs % 3600) // 60}m"


def _dashes(n: int) -> str:
    return "-" * n


def _md_table_header(cols: list[tuple[str, int]], f: IO[str]) -> None:
    """Write a Markdown table header row and separator row."""
    f.write("| " + " | ".join(f"{name:<{w}}" for name, w in cols) + " |\n")
    f.write("| " + " | ".join(_dashes(w) for _, w in cols) + " |\n")


# ══════════════════════════════════════════════════════════════════════════════
# § 4  GITHUB CLI HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _gh(*args: str) -> list[dict]:
    """Run `gh …` and return parsed JSON.  Raises on non-zero exit."""
    cmd = ["gh", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [gh error] {' '.join(cmd)}: {result.stderr.strip()}", file=sys.stderr)
        return []
    text = result.stdout.strip()
    if not text:
        return []
    return json.loads(text)


def _gh_str(*args: str) -> str:
    """Run `gh …` and return raw stdout string."""
    cmd = ["gh", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [gh error] {' '.join(cmd)}: {result.stderr.strip()}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def _parse_ts(s: str | None) -> datetime | None:
    """Parse ISO8601 string → tz-aware UTC datetime, or None."""
    if not s or s == "null":
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# § 5  EXTRACT
# ══════════════════════════════════════════════════════════════════════════════

# ── 5.1  Domain utilities (Layer 1) ──────────────────────────────────────────

# Odoo task link patterns (both legacy /web# and modern /odoo/ URL forms).
# Patterns are tried in order; matches from all are merged and deduplicated.
_ODOO_TASK_PATTERNS: list[re.Pattern] = [
    # Modern project task: /odoo/my-tasks/ID  or  /odoo/<anything>/tasks/ID
    re.compile(r"/odoo/(?:my-tasks|[^/\s]+/tasks)/(\d+)"),
    # Direct model route: /odoo/project.task/ID
    re.compile(r"/odoo/project\.task/(\d+)"),
    # Helpdesk ticket: /odoo/helpdesk.ticket/ID  (treated as a billable unit)
    re.compile(r"/odoo/helpdesk\.ticket/(\d+)"),
    # Legacy: /web#action=...&id=ID  or  /web#model=project.task&id=ID
    re.compile(r"/web#[^\s\"<>]*?id=(\d+)"),
]


def extract_tasks(branch: str, body: str) -> list[str]:
    """Return unique task IDs from branch name (leading digits) and PR body.

    Handles both modern Odoo URL format (/odoo/my-tasks/{id}) and legacy
    /web# fragment format.  All results are deduplicated.
    """
    seen: dict[str, bool] = {}
    tasks: list[str] = []
    m = re.match(r"^(\d+)", branch)
    if m:
        t = m.group(1)
        seen[t] = True
        tasks.append(t)
    for pat in _ODOO_TASK_PATTERNS:
        for t in pat.findall(body):
            if t not in seen:
                seen[t] = True
                tasks.append(t)
    return tasks


def _referenced_pr_numbers(body: str) -> list[int]:
    """Return referenced PR numbers from a body with no direct task links."""
    seen: set[int] = set()
    nums: list[int] = []
    for match in re.finditer(r"(?:#|pull/)\s*(\d+)", body):
        num = int(match.group(1))
        if num not in seen:
            seen.add(num)
            nums.append(num)
    return nums


def _extract_referenced_pr_tasks(repo: str, body: str) -> list[str]:
    """Infer tasks from explicitly referenced PRs when the current PR has none."""
    tasks: list[str] = []
    seen: set[str] = set()
    for num in _referenced_pr_numbers(body):
        meta = _fetch_pr_meta(repo, num)
        ref = meta.get("ref", "")
        pr_body = _gh_str("api", f"repos/{repo}/pulls/{num}", "--jq", '.body // ""')
        for task in extract_tasks(ref, pr_body):
            if task not in seen:
                seen.add(task)
                tasks.append(task)
    return tasks


def pr_overlaps_range(
    created_at: datetime | None, closed_at: datetime | None, config: Config
) -> bool:
    """Return True if the PR's lifetime overlaps the target range."""
    pr_start = created_at or config.range_start
    pr_end = closed_at or config.range_end
    return not (pr_end < config.range_start or pr_start >= config.range_end)


def _utc_z(ts: datetime) -> str:
    """Return an ISO-8601 UTC timestamp accepted by GitHub search."""
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _github_updated_range(config: Config) -> str:
    """Return GitHub search updated range for the configured ET window."""
    start = _utc_z(config.range_start)
    end = _utc_z(config.range_end - timedelta(seconds=1))
    return f"{start}..{end}"


def _gh_search_args(config: Config) -> list[str]:
    """Build common gh-search CLI arguments for PR queries."""
    args: list[str] = []
    if config.org:
        args += ["--owner", config.org]
    args += [
        "--updated",
        _github_updated_range(config),
        "--limit",
        "200",
    ]
    return args


# ── 5.2  Atomic API wrappers (Layer 2) ───────────────────────────────────────


def _fetch_pr_meta(repo: str, num: int) -> dict:
    """Fetch branch name and merged_at for a single PR."""
    raw = _gh_str(
        "api",
        f"repos/{repo}/pulls/{num}",
        "--jq",
        '{ref: .head.ref, merged_at: (.merged_at // "")}',
    )
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _fetch_pr_commit_dates(repo: str, num: int) -> list[tuple[datetime, str]]:
    """Fetch author timestamps and subjects for all commits on a PR."""
    commits = _gh("api", f"repos/{repo}/pulls/{num}/commits", "--paginate")
    result = []
    for c in commits:
        if not isinstance(c, dict):
            continue
        ts = _parse_ts(c.get("commit", {}).get("author", {}).get("date"))
        if ts:
            subject = c.get("commit", {}).get("message", "").splitlines()[0]
            result.append((ts, subject))
    return result


def _fetch_pr_reviews(repo: str, num: int, user: str) -> list[datetime]:
    """Fetch submitted_at timestamps of a user's reviews on a PR."""
    reviews = _gh("api", f"repos/{repo}/pulls/{num}/reviews", "--paginate")
    result = []
    for rev in reviews:
        if rev.get("user", {}).get("login") != user:
            continue
        ts = _parse_ts(rev.get("submitted_at"))
        if ts:
            result.append(ts)
    return result


def _resolve_commit_branch(sha: str, repo_dir: Path) -> str:
    """Return the first local branch containing sha, or empty string."""
    proc = subprocess.run(
        ["git", "branch", "--contains", sha],
        capture_output=True,
        text=True,
        cwd=repo_dir,
    )
    branches = [b.lstrip("* ").strip() for b in proc.stdout.splitlines() if b.strip()]
    return branches[0] if branches else ""


# ── 5.3  PR processors (Layer 3 — pure w.r.t. I/O, no config.diag calls) ────


def _build_review_events(
    tasks: list[str],
    repo: str,
    num: int,
    branch: str,
    times: list,
    title: str,
    body: str,
) -> list[RawEvent]:
    """Build REVIEW RawEvent list for a reviewed PR."""
    return [
        RawEvent(
            timestamp=t,
            task_ids=tasks or ["UNKNOWN"],
            repo=repo,
            pr_num=num,
            event_type=EventType.REVIEW,
            branch=branch,
            is_release=False,
            subject=title,
            pr_title=title,
            pr_body=body,
        )
        for t in times
    ]


def _build_authored_diag(
    repo,
    num,
    branch,
    body,
    tasks,
    outcome,
    created_at,
    closed_at,
    tagged,
    commit_ts,
    in_range,
    lifecycle_ir,
    source_events,
) -> PRDiagnostics:
    """Assemble PRDiagnostics for a successfully processed authored PR."""
    return PRDiagnostics(
        repo=repo,
        num=num,
        branch=branch,
        body=body,
        tasks=tasks,
        outcome=outcome,
        created_at=created_at,
        closed_at=closed_at,
        timestamps=tagged,
        fetched_count=len(commit_ts),
        in_range_count=len(in_range),
        lifecycle_in_range_count=len(lifecycle_ir),
        source_events=source_events,
    )


def _build_reviewed_diag(
    repo,
    num,
    branch,
    body,
    tasks,
    created_at,
    closed_at,
    tagged,
    review_times,
    in_range_reviews,
    num_tasks,
) -> PRDiagnostics:
    """Assemble PRDiagnostics for a successfully processed reviewed PR."""
    return PRDiagnostics(
        repo=repo,
        num=num,
        branch=branch,
        body=body,
        tasks=tasks,
        outcome="reviewed",
        created_at=created_at,
        closed_at=closed_at,
        timestamps=tagged,
        fetched_count=len(review_times),
        in_range_count=len(in_range_reviews),
        lifecycle_in_range_count=0,
        source_events=[
            ("review", timestamp, num_tasks) for timestamp in in_range_reviews
        ],
    )


def _parse_pr_search_row(
    pr: dict,
) -> tuple[int, str, str, str, datetime | None, datetime | None]:
    """Extract common fields from one GitHub PR search result row."""
    return (
        pr["number"],
        pr["repository"]["nameWithOwner"],
        pr.get("title") or "",
        pr.get("body") or "",
        _parse_ts(pr.get("createdAt")),
        _parse_ts(pr.get("closedAt")),
    )


def _make_skip_diag(
    repo: str,
    num: int,
    body: str,
    outcome: str,
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
    branch: str = "",
    tasks: list[str] | None = None,
) -> PRDiagnostics:
    """Build a PRDiagnostics for a skipped PR (no timestamps, no events)."""
    return PRDiagnostics(
        repo=repo,
        num=num,
        branch=branch,
        body=body,
        tasks=tasks or [],
        outcome=outcome,
        created_at=created_at,
        closed_at=closed_at,
    )


def _classify_pr_timestamps(
    commit_ts: list[datetime],
    lifecycle_ts: list[datetime],
    config: Config,
) -> tuple[list[datetime], list[datetime], list[tuple]]:
    """Partition and tag all PR timestamps.

    Returns (in_range_commits, lifecycle_in_range, tagged_all).
    tagged_all entries are (ts, kind, in_range).
    """
    in_range_commits = [t for t in commit_ts if config.in_range(t)]
    lifecycle_ir = [t for t in lifecycle_ts if config.in_range(t)]
    commit_set = set(commit_ts)
    tagged = [
        (t, "commit" if t in commit_set else "lifecycle", config.in_range(t))
        for t in sorted(commit_ts + lifecycle_ts)
    ]
    return in_range_commits, lifecycle_ir, tagged


def _pr_raw_event(
    timestamp: datetime,
    event_type: EventType,
    repo: str,
    num: int,
    branch: str,
    tasks: list[str],
    title: str,
    body: str,
    subject: str,
) -> RawEvent:
    """Build one PR-backed RawEvent with description context."""
    return RawEvent(
        timestamp=timestamp,
        task_ids=tasks,
        repo=repo,
        pr_num=num,
        event_type=event_type,
        branch=branch,
        is_release=len(tasks) > 1,
        subject=subject,
        pr_title=title,
        pr_body=body,
    )


def _build_pr_commit_events(
    repo: str,
    num: int,
    branch: str,
    tasks: list[str],
    in_range_commits: list[tuple[datetime, str]],
    merged_at: datetime | None,
    title: str,
    body: str,
    config: Config,
) -> tuple[list[RawEvent], list[tuple]]:
    """Build COMMIT and optional MERGED RawEvents for one PR."""
    events = [
        _pr_raw_event(
            t, EventType.COMMIT, repo, num, branch, tasks, title, body, subject
        )
        for t, subject in in_range_commits
    ]
    source_events: list[tuple] = []
    if merged_at and config.in_range(merged_at):
        events.append(
            _pr_raw_event(
                merged_at, EventType.MERGE, repo, num, branch, tasks, title, body, title
            )
        )
        source_events.append(("merge", merged_at, len(tasks)))
    return events, source_events


def _fetch_authored_pr_activity(
    repo: str,
    num: int,
    body: str,
    created_at: datetime | None,
    closed_at: datetime | None,
    config: Config,
) -> tuple[
    str,
    datetime | None,
    list[str],
    list[datetime],
    list[tuple[datetime, str]],
    list[datetime],
    list[tuple],
]:
    """Fetch and classify activity timestamps for one authored PR."""
    meta = _fetch_pr_meta(repo, num)
    branch = meta.get("ref", "")
    merged_at = _parse_ts(meta.get("merged_at") or None)
    tasks = extract_tasks(branch, body) or _extract_referenced_pr_tasks(repo, body)
    tasks = tasks or ["UNKNOWN"]
    commit_items = _fetch_pr_commit_dates(repo, num)
    commit_ts = [timestamp for timestamp, _subject in commit_items]
    lifecycle_ts = [t for t in [created_at, closed_at] if t]
    in_range, lifecycle_ir, tagged = _classify_pr_timestamps(
        commit_ts, lifecycle_ts, config
    )
    in_range_set = set(in_range)
    in_range_items = [item for item in commit_items if item[0] in in_range_set]
    return branch, merged_at, tasks, commit_ts, in_range_items, lifecycle_ir, tagged


def _process_authored_pr(
    pr: dict, config: Config
) -> tuple[list[RawEvent], PRDiagnostics]:
    """Fetch, classify, and build events + diag payload for one authored PR. No I/O."""
    num, repo, title, body, created_at, closed_at = _parse_pr_search_row(pr)

    if not pr_overlaps_range(created_at, closed_at, config):
        return [], _make_skip_diag(
            repo, num, body, "skip_overlap", created_at, closed_at
        )

    branch, merged_at, tasks, commit_ts, in_range, lifecycle_ir, tagged = (
        _fetch_authored_pr_activity(repo, num, body, created_at, closed_at, config)
    )

    if not in_range and not lifecycle_ir:
        return [], _make_skip_diag(
            repo, num, body, "skip_no_activity", created_at, closed_at, branch, tasks
        )

    events, source_events = _build_pr_commit_events(
        repo, num, branch, tasks, in_range, merged_at, title, body, config
    )
    outcome = "release" if len(tasks) > 1 else "authored"
    diag = _build_authored_diag(
        repo,
        num,
        branch,
        body,
        tasks,
        outcome,
        created_at,
        closed_at,
        tagged,
        commit_ts,
        in_range,
        lifecycle_ir,
        source_events,
    )
    return events, diag


def _process_reviewed_pr(
    pr: dict, config: Config
) -> tuple[list[RawEvent], PRDiagnostics]:
    """Fetch reviews and build events + diag payload for one reviewed PR. No I/O."""
    num, repo, title, body, created_at, closed_at = _parse_pr_search_row(pr)

    if not pr_overlaps_range(created_at, closed_at, config):
        return [], _make_skip_diag(
            repo, num, body, "skip_overlap", created_at, closed_at
        )

    branch = _gh_str("api", f"repos/{repo}/pulls/{num}", "--jq", ".head.ref")
    tasks = extract_tasks(branch, body)
    review_times = _fetch_pr_reviews(repo, num, config.gh_user)
    in_range_reviews = [t for t in review_times if config.in_range(t)]
    tagged = [(t, "review", config.in_range(t)) for t in sorted(review_times)]

    if not review_times:
        return [], _make_skip_diag(
            repo, num, body, "skip_no_activity", created_at, closed_at, branch, tasks
        )

    events = _build_review_events(
        tasks, repo, num, branch, in_range_reviews, title, body
    )
    diag = _build_reviewed_diag(
        repo,
        num,
        branch,
        body,
        tasks,
        created_at,
        closed_at,
        tagged,
        review_times,
        in_range_reviews,
        max(len(tasks), 1),
    )
    return events, diag


def _git_log_proc(repo_dir: Path, config: Config) -> subprocess.CompletedProcess:
    """Run git log for in-window commits in one local repo."""
    after = (config.range_start - timedelta(seconds=1)).isoformat()
    before = config.range_end.isoformat()
    return subprocess.run(
        [
            "git",
            "log",
            "--all",
            "--branches",
            f"--author={config.gh_user}",
            f"--after={after}",
            f"--before={before}",
            "--format=%H\t%aI\t%s",
        ],
        capture_output=True,
        text=True,
        cwd=repo_dir,
    )


def _git_remote_origin(repo_dir: Path) -> str:
    """Return the local repo's origin URL, or an empty string."""
    proc = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        cwd=repo_dir,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _repo_name_from_remote(origin: str) -> str:
    """Return owner/repo from a GitHub remote URL, or an empty string."""
    patterns: list[str] = [
        r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$",
        r"github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, origin)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return ""


def _local_repo_name(repo_dir: Path) -> str:
    """Return the canonical owner/repo name for a local git repository."""
    return _repo_name_from_remote(_git_remote_origin(repo_dir)) or str(repo_dir.name)


def _process_local_repo(
    repo_dir: Path, config: Config
) -> tuple[list[RawEvent], list[LocalCommitDiag]]:
    """Scan one local .git directory for in-range commits.

    Returns (events, diag_payloads). Does not call config.diag.
    """
    proc = _git_log_proc(repo_dir, config)
    if proc.returncode != 0:
        return [], []
    events: list[RawEvent] = []
    diags: list[LocalCommitDiag] = []
    repo_name = _local_repo_name(repo_dir)
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        sha, ts_str, subject = parts[0], parts[1], parts[2]
        ts = _parse_ts(ts_str)
        if ts is None or not config.in_range(ts):
            continue
        branch = _resolve_commit_branch(sha, repo_dir)
        task_ids = extract_tasks(branch, subject) or ["UNKNOWN"]
        events.append(
            RawEvent(
                timestamp=ts,
                task_ids=task_ids,
                repo=repo_name,
                pr_num=0,
                event_type=EventType.COMMIT,
                branch=branch,
                is_release=len(task_ids) > 1,
                subject=subject,
            )
        )
        diags.append(LocalCommitDiag(repo_name, ts, branch, task_ids))
    return events, diags


def _local_cache_path(config: Config) -> Path:
    """Return the configured local-event cache path."""
    return Path(config.local_events_cache_file).expanduser()


def _raw_event_to_cache(event: RawEvent) -> dict:
    """Serialize one local RawEvent to JSON-safe data."""
    return {
        "timestamp": event.timestamp.isoformat(),
        "task_ids": event.task_ids,
        "repo": event.repo,
        "pr_num": event.pr_num,
        "event_type": event.event_type.name,
        "branch": event.branch,
        "is_release": event.is_release,
        "subject": event.subject,
    }


def _raw_event_from_cache(data: dict) -> RawEvent | None:
    """Deserialize one cached local RawEvent, or None if invalid."""
    ts = _parse_ts(data.get("timestamp"))
    event_type = EventType.__members__.get(data.get("event_type", ""))
    if not ts or not event_type:
        return None
    return RawEvent(
        timestamp=ts,
        task_ids=list(data.get("task_ids") or ["UNKNOWN"]),
        repo=str(data.get("repo") or ""),
        pr_num=int(data.get("pr_num") or 0),
        event_type=event_type,
        branch=str(data.get("branch") or ""),
        is_release=bool(data.get("is_release")),
        subject=str(data.get("subject") or ""),
    )


def _local_cache_meta(config: Config) -> dict:
    """Return cache metadata used to avoid wrong-window reuse."""
    return {
        "gh_user": config.gh_user,
        "start_date": config.start_date.isoformat(),
        "end_date": config.end_date.isoformat(),
    }


def _write_local_events_cache(events: list[RawEvent], config: Config) -> None:
    """Write local events to cache for host/container handoff."""
    path = _local_cache_path(config)
    payload = {
        "meta": _local_cache_meta(config),
        "events": [_raw_event_to_cache(e) for e in events],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"  [local cache] wrote {len(events)} events to {path}", file=sys.stderr)


def _read_local_events_cache(config: Config) -> list[RawEvent]:
    """Read local events from cache when local repos are unavailable."""
    path = _local_cache_path(config)
    if not path.exists():
        print(f"  [local cache] {path} not found", file=sys.stderr)
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("meta") != _local_cache_meta(config):
        print(f"  [local cache] {path} metadata mismatch; ignoring", file=sys.stderr)
        return []
    events = [_raw_event_from_cache(item) for item in payload.get("events", [])]
    return [event for event in events if event is not None]


# ── 5.4  Diagnostic writer for PRs (Layer 4) ─────────────────────────────────


def _log_pr_diagnostics(diag: PRDiagnostics, config: Config) -> None:
    """Write all diagnostic lines for one processed PR to config.diag."""
    if config.diag is None:
        return
    config.diag.github(diag.repo, diag.num)
    if diag.outcome == "skip_overlap":
        config.diag.skip_overlap(diag.repo, diag.num, diag.created_at, diag.closed_at)
        return
    config.diag.task_extract(diag.branch, diag.body, diag.tasks)
    for ts, kind, in_range in diag.timestamps:
        config.diag.ts("[in] " if in_range else "[out]", ts, kind)
    config.diag.commit_summary(
        fetched=diag.fetched_count,
        in_range_commits=diag.in_range_count,
        lifecycle_in_range=diag.lifecycle_in_range_count,
    )
    if diag.outcome == "skip_no_activity":
        config.diag.skip_no_activity(diag.repo, diag.num, diag.branch)
        return
    if diag.outcome == "authored":
        config.diag.authored(diag.repo, diag.num, diag.branch, diag.tasks[0])
    elif diag.outcome == "release":
        config.diag.release(diag.repo, diag.num, diag.branch, diag.tasks)
    elif diag.outcome == "reviewed":
        config.diag.reviewed(
            diag.repo, diag.num, diag.branch, diag.tasks[0] if diag.tasks else "UNKNOWN"
        )
    for event_kind, ts, num_tasks in diag.source_events:
        config.diag.source_event(event_kind, ts, num_tasks)


# ── 5.5  Phase fetch functions (Layer 4) ─────────────────────────────────────


def fetch_local_commits(config: Config) -> tuple[list[RawEvent], list[LocalCommitDiag]]:
    """Scan all local .git repos under config.local_repos_path; return events."""
    if not config.local_repos_path:
        return [], []
    root = Path(config.local_repos_path).expanduser()
    if not root.exists():
        print(f"  [local] {root} does not exist, using cache", file=sys.stderr)
        events = _read_local_events_cache(config)
        diags = [
            LocalCommitDiag(e.repo, e.timestamp, e.branch, e.task_ids) for e in events
        ]
        return events, diags
    all_events: list[RawEvent] = []
    all_diags: list[LocalCommitDiag] = []
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
            continue
        events, diags = _process_local_repo(repo_dir, config)
        all_events.extend(events)
        all_diags.extend(diags)
    _write_local_events_cache(all_events, config)
    return all_events, all_diags


def fetch_authored_prs(config: Config) -> tuple[list[RawEvent], list[PRDiagnostics]]:
    """Search for authored PRs; process each; return events and diagnostics."""
    print("Phase 1: Fetching authored PRs...", file=sys.stderr)
    prs = _gh(
        "search",
        "prs",
        "--author",
        config.gh_user,
        "--json",
        "number,repository,title,body,createdAt,closedAt",
        *_gh_search_args(config),
    )
    print(f"  Found {len(prs)} authored PRs", file=sys.stderr)
    all_events: list[RawEvent] = []
    diagnostics: list[PRDiagnostics] = []
    for pr in prs:
        events, diag = _process_authored_pr(pr, config)
        num = pr["number"]
        repo = pr["repository"]["nameWithOwner"]
        if diag.outcome.startswith("skip"):
            print(f"  PR #{num}  {repo}  skipped ({diag.outcome})", file=sys.stderr)
        else:
            print(f"  PR #{num}  {repo}  tasks={diag.tasks}", file=sys.stderr)
        all_events.extend(events)
        diagnostics.append(diag)
    return all_events, diagnostics


def fetch_reviewed_prs(config: Config) -> tuple[list[RawEvent], list[PRDiagnostics]]:
    """Search for reviewed PRs; process each; return events and diagnostics."""
    print("Phase 2: Fetching reviewed PRs...", file=sys.stderr)
    prs = _gh(
        "search",
        "prs",
        "--reviewed-by",
        config.gh_user,
        "--json",
        "number,repository,title,body,author,createdAt,closedAt",
        *_gh_search_args(config),
    )
    print(f"  Found {len(prs)} reviewed PRs", file=sys.stderr)
    all_events: list[RawEvent] = []
    diagnostics: list[PRDiagnostics] = []
    for pr in prs:
        if pr.get("author", {}).get("login") == config.gh_user:
            continue  # handled in Phase 1
        events, diag = _process_reviewed_pr(pr, config)
        num = pr["number"]
        repo = pr["repository"]["nameWithOwner"]
        if diag.outcome.startswith("skip"):
            print(
                f"  Reviewed PR #{num}  {repo}  skipped ({diag.outcome})",
                file=sys.stderr,
            )
        else:
            print(
                f"  Reviewed PR #{num}  {repo}  tasks={diag.tasks or ['UNKNOWN']}",
                file=sys.stderr,
            )
        all_events.extend(events)
        diagnostics.append(diag)
    return all_events, diagnostics


# ── 5.6  Deduplication (Layer 4) ─────────────────────────────────────────────


def _deduplicate_events(
    events: list[RawEvent],
) -> tuple[list[RawEvent], list[tuple]]:
    """Remove duplicate events; return (deduped, dropped_for_logging)."""
    seen: dict[tuple, RawEvent] = {}
    dropped: list[tuple] = []
    for ev in events:
        key = (
            round(ev.timestamp.timestamp()),
            ev.repo,
            tuple(ev.task_ids),
            ev.event_type,
        )
        if key not in seen:
            seen[key] = ev
        elif seen[key].pr_num == 0:
            seen[key] = ev
        else:
            dropped.append((ev.timestamp, ev.repo, ev.task_ids, seen[key].pr_num))
    return sorted(seen.values(), key=lambda e: e.timestamp), dropped


# ── 5.7  Dedup diagnostic writer (Layer 5) ───────────────────────────────────


def _log_dedup_drops(dropped: list[tuple], config: Config) -> None:
    """Write diagnostic lines for events removed by deduplication."""
    if not config.diag:
        return
    for ts, repo, tasks, kept_pr_num in dropped:
        config.diag.dedup_skip(ts, repo, tasks, kept_pr_num)


def _log_local_commit_diagnostics(
    diagnostics: list[LocalCommitDiag], config: Config
) -> None:
    """Write diagnostic lines for local-git commits."""
    for diag in diagnostics:
        print(
            f"  [local] {diag.ts}  {diag.repo_dir}  "
            f"branch={diag.branch}  tasks={diag.tasks}",
            file=sys.stderr,
        )
        if config.diag:
            config.diag.local_commit(diag.repo_dir, diag.ts, diag.branch, diag.tasks)


def _log_pr_diagnostics_batch(diagnostics: list[PRDiagnostics], config: Config) -> None:
    """Write diagnostic lines for processed PRs."""
    for diag in diagnostics:
        _log_pr_diagnostics(diag, config)


# ── 5.8  Extract orchestrator (Layer 5) ──────────────────────────────────────


def extract(config: Config) -> list[RawEvent]:
    """E in ETL: collect all events from all sources, normalised to RawEvent."""
    local_events, local_diags = fetch_local_commits(config)
    authored_events, authored_diags = fetch_authored_prs(config)
    reviewed_events, reviewed_diags = fetch_reviewed_prs(config)
    all_events = local_events + authored_events + reviewed_events
    deduped, dedup_dropped = _deduplicate_events(all_events)
    _log_local_commit_diagnostics(local_diags, config)
    _log_pr_diagnostics_batch(authored_diags + reviewed_diags, config)
    _log_dedup_drops(dedup_dropped, config)
    return deduped


# ══════════════════════════════════════════════════════════════════════════════

# § 6  TRANSFORM
# ══════════════════════════════════════════════════════════════════════════════

# ── 6.1  Primitive helpers (Layer 1/2) ───────────────────────────────────────


def _ceil_to_billing_step(secs: float, config: Config) -> float:
    """Round secs UP to the nearest billing_step_mins boundary."""
    step = config.billing_step_mins * 60.0
    if step <= 0 or secs <= 0:
        return secs
    return math.ceil(secs / step) * step


# ── 6.2  Window computation (Layer 2/3) ──────────────────────────────────────


def compute_windows(
    timestamps: list[datetime],
    gap_secs: int,
    config: Config,
) -> list[tuple[datetime, datetime]]:
    """Partition sorted timestamps into sessions separated by gaps > gap_secs.

    Duration: each session's elapsed time is rounded UP to the nearest
    billing_step_mins boundary, then floored to min_task_secs.  The floor
    is applied once per SESSION, not per commit.

    Monotonicity guarantee: when config.sweep_min_gap_mins >= 2*min_task_minutes,
    any two sessions that merge are at least 2*floor apart, so merged elapsed
    always exceeds the sum of the two floored originals.
    """
    if not timestamps:
        return []
    sorted_ts = sorted(timestamps)
    windows: list[tuple[datetime, datetime]] = []
    win_start = sorted_ts[0]

    for i in range(1, len(sorted_ts)):
        gap = (sorted_ts[i] - sorted_ts[i - 1]).total_seconds()
        if gap > gap_secs:
            elapsed = (sorted_ts[i - 1] - win_start).total_seconds()
            dur = max(
                _ceil_to_billing_step(elapsed, config), float(config.min_task_secs)
            )
            windows.append((win_start, win_start + timedelta(seconds=dur)))
            win_start = sorted_ts[i]

    elapsed = (sorted_ts[-1] - win_start).total_seconds()
    dur = max(_ceil_to_billing_step(elapsed, config), float(config.min_task_secs))
    windows.append((win_start, win_start + timedelta(seconds=dur)))
    return windows


# ── 6.3  Utilisation scoring (Layer 2) ───────────────────────────────────────


def score_gap(total_secs: float, num_days: int, config: Config) -> float:
    """Piecewise utilisation score.  Best gap = argmax score_gap.

    x = total_secs / 3600 / num_days  (average hours worked per day)

    Strict C0 continuity: f(B_LOW) = S_LOW, f(B_HIGH) = S_HIGH exactly.

         ⎧  S_LOW  − expm1(k1·(B_LOW  − x))                      x < B_LOW
    f =  ⎨  S_LOW  + (S_HIGH−S_LOW) · expm1(k2·(x−B_LOW))        B_LOW ≤ x ≤ B_HIGH
         ⎪                             ──────────────────────────────────
         ⎪                             expm1(k2·(B_HIGH−B_LOW))
         ⎩  S_HIGH − expm1(k3·(x  − B_HIGH))                     x > B_HIGH
    """
    x = total_secs / 3600.0 / max(num_days, 1)
    b_low, b_high = config.b_low, config.b_high
    s_low, s_high = config.s_low, config.s_high
    k1, k2, k3 = config.k1, config.k2, config.k3
    if x < b_low:
        return s_low - math.expm1(k1 * (b_low - x))
    elif x <= b_high:
        denom = math.expm1(k2 * (b_high - b_low))
        if denom == 0:
            t = (x - b_low) / (b_high - b_low) if b_high > b_low else 0.0
            return s_low + (s_high - s_low) * t
        return s_low + (s_high - s_low) * math.expm1(k2 * (x - b_low)) / denom
    else:
        return s_high - math.expm1(k3 * (x - b_high))


def _score_day(total_secs: float, config: Config) -> float:
    """Return the score for one target day's total seconds."""
    return score_gap(total_secs, 1, config)


# ── 6.4  Entry builders (Layer 2/3) ──────────────────────────────────────────


class SessionizationStrategy(ABC):
    """Strategy interface for interchangeable sessionization algorithms."""

    def __init__(self, settings: SessionStrategyConfig) -> None:
        self.settings = settings

    @abstractmethod
    def build_entries(
        self, group: "StrategyEventGroup", config: Config, gap_secs: int | None = None
    ) -> list[TimeEntry]:
        """Build TimeEntry rows for one strategy group."""

    def description_context(self, entry: TimeEntry) -> list[str]:
        """Return configured description context for an entry."""
        return _strategy_context_lines(entry, self.settings)

    def default_action(self, entry: TimeEntry) -> str:
        """Return the configured fallback action phrase for an entry."""
        _ = entry
        return self.settings.fallback_action


@dataclass(frozen=True)
class StrategyEventGroup:
    """Events grouped for one strategy invocation."""

    strategy: SessionizationStrategy
    key: tuple
    events: list[RawEvent]


class WindowedSessionStrategy(SessionizationStrategy):
    """Sessionize grouped timestamps with a configurable gap."""

    def build_entries(
        self, group: StrategyEventGroup, config: Config, gap_secs: int | None = None
    ) -> list[TimeEntry]:
        gap = self.settings.gap_secs if gap_secs is None else gap_secs
        entries: list[TimeEntry] = []
        timestamps = [event.timestamp for event in group.events]
        for start, end in compute_windows(timestamps, gap, config):
            source_events = _events_in_window(group.events, start, end)
            entries.append(_strategy_time_entry(group, source_events, start, end))
        return entries


class FixedDurationStrategy(SessionizationStrategy):
    """Create one fixed-duration entry for each source event."""

    def build_entries(
        self, group: StrategyEventGroup, config: Config, gap_secs: int | None = None
    ) -> list[TimeEntry]:
        _ = config, gap_secs
        return [
            _strategy_time_entry(
                group, [event], event.timestamp, _fixed_entry_end(event, self.settings)
            )
            for event in sorted(group.events, key=lambda event: event.timestamp)
        ]


class SessionizationContext:
    """Context that delegates sessionization to Strategy objects."""

    def __init__(self, strategies: list[SessionizationStrategy]) -> None:
        self.strategies = strategies
        self.by_event_type = _strategy_event_type_map(strategies)

    def groups(self, events: list[RawEvent]) -> list[StrategyEventGroup]:
        """Classify and group events for strategy execution."""
        groups: dict[tuple, list[RawEvent]] = {}
        strategy_by_key: dict[tuple, SessionizationStrategy] = {}
        for event in events:
            for task_id in event.task_ids or ["UNKNOWN"]:
                strategy = self.by_event_type[event.event_type]
                key = _strategy_group_key(strategy.settings, event, task_id)
                groups.setdefault(key, []).append(event)
                strategy_by_key[key] = strategy
        return [
            StrategyEventGroup(strategy_by_key[key], key, value)
            for key, value in groups.items()
        ]

    def build_entries(
        self, events: list[RawEvent], gap_secs: int | None, config: Config
    ) -> list[TimeEntry]:
        """Build sorted entries by delegating to configured strategies."""
        entries: list[TimeEntry] = []
        for group in self.groups(events):
            gap = gap_secs if group.strategy.settings.sweep_enabled else None
            entries.extend(group.strategy.build_entries(group, config, gap))
        return sorted(
            entries, key=lambda entry: (entry.start, entry.repo, entry.task_id)
        )


_STRATEGY_CLASSES: dict[str, type[SessionizationStrategy]] = {
    "session": WindowedSessionStrategy,
    "fixed": FixedDurationStrategy,
}


DEFAULT_SESSION_STRATEGY_CONFIGS = (
    SessionStrategyConfig(
        "development",
        "Development",
        (EventType.COMMIT,),
        "session",
        ("strategy", "repo", "task_id"),
        sweep_enabled=True,
        fallback_action="advanced project implementation",
    ),
    SessionStrategyConfig(
        "merge",
        "Merge",
        (EventType.MERGE,),
        "fixed",
        ("strategy", "repo", "task_id", "event_type", "pr_num", "timestamp"),
        fallback_action="completed release update",
    ),
    SessionStrategyConfig(
        "review",
        "Review",
        (EventType.REVIEW,),
        "fixed",
        ("strategy", "repo", "task_id", "event_type", "pr_num", "timestamp"),
        fallback_action="validated project changes",
    ),
)


def _fixed_entry_end(event: RawEvent, settings: SessionStrategyConfig) -> datetime:
    """Return the end timestamp for one fixed-duration strategy entry."""
    return event.timestamp + timedelta(seconds=settings.fixed_secs)


def _strategy_event_type_map(
    strategies: list[SessionizationStrategy],
) -> dict[EventType, SessionizationStrategy]:
    """Return event-type to strategy map, rejecting duplicate coverage."""
    by_type: dict[EventType, SessionizationStrategy] = {}
    for strategy in strategies:
        for event_type in strategy.settings.event_types:
            if event_type in by_type:
                raise ValueError(f"duplicate strategy for {event_type.name}")
            by_type[event_type] = strategy
    _validate_strategy_coverage(by_type)
    return by_type


def _validate_strategy_coverage(
    by_type: dict[EventType, SessionizationStrategy],
) -> None:
    """Raise if any EventType lacks a configured strategy."""
    missing = set(EventType) - set(by_type)
    if missing:
        names = ", ".join(sorted(event_type.name for event_type in missing))
        raise ValueError(f"missing strategy for {names}")


def _strategy_from_settings(settings: SessionStrategyConfig) -> SessionizationStrategy:
    """Instantiate the concrete Strategy configured by one settings row."""
    strategy_class = _STRATEGY_CLASSES.get(settings.strategy_kind)
    if strategy_class is None:
        raise ValueError(f"unknown strategy kind {settings.strategy_kind}")
    return strategy_class(settings)


def _make_sessionization_context(
    settings_rows: tuple[SessionStrategyConfig, ...] = DEFAULT_SESSION_STRATEGY_CONFIGS,
) -> SessionizationContext:
    """Build the default strategy context from flat config rows."""
    strategies = [
        _strategy_from_settings(settings)
        for settings in sorted(settings_rows, key=lambda row: row.priority)
    ]
    return SessionizationContext(strategies)


def _strategy_group_key(
    settings: SessionStrategyConfig, event: RawEvent, task_id: str
) -> tuple:
    """Return a data-driven grouping key for one event/task pair."""
    values = {
        "strategy": settings.name,
        "repo": event.repo,
        "task_id": task_id,
        "event_type": event.event_type.name,
        "pr_num": event.pr_num,
        "timestamp": event.timestamp.isoformat(),
    }
    return tuple(values[name] for name in settings.group_keys)


def _strategy_key_value(key: tuple, settings: SessionStrategyConfig, name: str) -> str:
    """Return a named value from a configured strategy group key."""
    return str(key[settings.group_keys.index(name)])


def _strategy_time_entry(
    group: StrategyEventGroup,
    source_events: list[RawEvent],
    start: datetime,
    end: datetime,
) -> TimeEntry:
    """Build one TimeEntry from a strategy result."""
    settings = group.strategy.settings
    repo = _strategy_key_value(group.key, settings, "repo")
    task_id = _strategy_key_value(group.key, settings, "task_id")
    return TimeEntry(
        task_id=task_id,
        repo=repo,
        pr_num=source_events[0].pr_num if source_events else 0,
        start=start,
        end=end,
        label=repo,
        branch=_entry_branch(source_events),
        source_events=source_events,
        strategy_name=settings.name,
        strategy_category=settings.category,
        activity_type=source_events[0].event_type.name if source_events else "",
    )


def _group_events_by_repo_task(
    events: list[RawEvent],
) -> dict[tuple, list[RawEvent]]:
    """Group all events by repo and individual task id."""
    groups: dict[tuple, list[RawEvent]] = {}
    for ev in events:
        for task_id in ev.task_ids or ["UNKNOWN"]:
            key = (ev.repo, task_id)
            groups.setdefault(key, []).append(ev)
    return groups


def _events_in_window(
    events: list[RawEvent], start: datetime, end: datetime
) -> list[RawEvent]:
    """Return source events inside one computed TimeEntry window."""
    return [event for event in events if start <= event.timestamp <= end]


def _entry_branch(events: list[RawEvent]) -> str:
    """Return a compact branch context string for source events."""
    return ", ".join(sorted({event.branch for event in events if event.branch}))


def _entries_for_group(
    key: tuple,
    events: list[RawEvent],
    gap_secs: int,
    config: Config,
) -> list[TimeEntry]:
    """Build TimeEntry rows for one repo+task commit window group."""
    repo, task_id = key
    label = repo
    task_ids = [task_id]
    entries: list[TimeEntry] = []
    timestamps = [event.timestamp for event in events]
    for ws, we in compute_windows(timestamps, gap_secs, config):
        total_secs = int((we - ws).total_seconds())
        base_secs, remainder = divmod(total_secs, len(task_ids))
        source_events = _events_in_window(events, ws, we)
        for index, task_id in enumerate(task_ids):
            entry_secs = base_secs + (1 if index < remainder else 0)
            entries.append(
                TimeEntry(
                    task_id=task_id,
                    repo=repo,
                    pr_num=0,
                    start=ws,
                    end=ws + timedelta(seconds=entry_secs),
                    label=label,
                    branch=_entry_branch(source_events),
                    source_events=source_events,
                )
            )
    return entries


def _build_window_entries(
    events: list[RawEvent], gap_secs: int, config: Config
) -> list[TimeEntry]:
    """Build entries through the sessionization strategy context."""
    context = _make_sessionization_context(config.session_strategy_configs)
    return context.build_entries(events, gap_secs, config)


# ── 6.5  Gap sweep (Layer 3/4) ───────────────────────────────────────────────


def _target_day_totals(entries: list[TimeEntry], config: Config) -> dict[date, float]:
    """Return target-date totals, splitting windows at ET midnight."""
    totals = {target_date: 0.0 for target_date in config.target_dates}
    for entry in entries:
        cursor = entry.start.astimezone(ET)
        end = entry.end.astimezone(ET)
        while cursor < end:
            next_day = cursor.date() + timedelta(days=1)
            midnight = datetime(next_day.year, next_day.month, next_day.day, tzinfo=ET)
            segment_end = min(end, midnight)
            if cursor.date() in totals:
                totals[cursor.date()] += (segment_end - cursor).total_seconds()
            cursor = segment_end
    return totals


def _score_entries_by_target_day(entries: list[TimeEntry], config: Config) -> float:
    """Return the average score after scoring each target day independently."""
    day_totals = _target_day_totals(entries, config)
    if not day_totals:
        return _score_day(0.0, config)
    return sum(_score_day(total, config) for total in day_totals.values()) / len(
        day_totals
    )


def _sweep_gap(
    events: list[RawEvent],
    gap_mins: int,
    config: Config,
) -> tuple[dict[str, float], float, float]:
    """Return task totals, combined total, and daily score for one gap."""
    task_sums: dict[str, float] = {}
    entries = _build_window_entries(events, gap_mins * 60, config)
    for e in entries:
        dur = (e.end - e.start).total_seconds()
        task_sums[e.task_id] = task_sums.get(e.task_id, 0.0) + dur
    return (
        task_sums,
        sum(task_sums.values()),
        _score_entries_by_target_day(entries, config),
    )


def _sweep_all_gaps(
    events: list[RawEvent],
    gap_vals: list[int],
    config: Config,
) -> tuple[list[dict], list[float], list[float], set[str]]:
    """Compute task_sums, combined totals, and scores for every gap value."""
    all_task_sums: list[dict[str, float]] = []
    combined: list[float] = []
    scores: list[float] = []
    all_task_ids: set[str] = set()
    for gap in gap_vals:
        sums, total, score = _sweep_gap(events, gap, config)
        all_task_ids.update(sums.keys())
        all_task_sums.append(sums)
        combined.append(total)
        scores.append(score)
    return all_task_sums, combined, scores, all_task_ids


def _find_best_gap(
    gap_vals: list[int],
    combined: list[float],
    scores: list[float],
    config: Config,
) -> int:
    """Return the index of the best gap value.

    Scores are already computed per target day, so gap selection should not use
    combined totals as a fallback; that would reintroduce cross-day skew.
    """
    return max(range(len(scores)), key=lambda i: (scores[i], -gap_vals[i]))


def _build_per_task_matrix(
    gap_vals: list[int],
    all_task_sums: list[dict[str, float]],
    all_task_ids: set[str],
) -> dict[str, list[float]]:
    """Build per-task totals matrix from cached sweep results."""
    per_task: dict[str, list[float]] = {tid: [] for tid in sorted(all_task_ids)}
    for sums in all_task_sums:
        for tid in sorted(all_task_ids):
            per_task[tid].append(sums.get(tid, 0.0))
    return per_task


def _billable_events(events: list[RawEvent]) -> list[RawEvent]:
    """Return events with resolved task IDs only."""
    return [
        event for event in events if event.task_ids and "UNKNOWN" not in event.task_ids
    ]


def sweep(events: list[RawEvent], config: Config) -> SweepResults:
    """Test all gap values; find the one with the best utilisation score."""
    print("Phase 3: Window gap sweep...", file=sys.stderr)
    gap_vals = list(
        range(
            config.sweep_min_gap_mins,
            config.sweep_max_gap_mins + 1,
            config.sweep_step_mins,
        )
    )
    all_task_sums, combined, scores, all_task_ids = _sweep_all_gaps(
        events, gap_vals, config
    )
    per_task = _build_per_task_matrix(gap_vals, all_task_sums, all_task_ids)
    obs_mean = sum(combined) / len(combined) if combined else 0.0
    best_idx = _find_best_gap(gap_vals, combined, scores, config)
    return SweepResults(
        gap_vals=gap_vals,
        combined=combined,
        per_task=per_task,
        scores=scores,
        obs_mean=obs_mean,
        best_gap=gap_vals[best_idx],
        best_total=combined[best_idx],
        best_score=scores[best_idx],
        num_days=config.num_days,
    )


# ── 6.6  Transform orchestrator (Layer 5) ────────────────────────────────────


def transform(events: list[RawEvent], config: Config) -> TransformResult:
    """T in ETL: compute all time entries and sweep results from raw events."""
    events_for_billing = _billable_events(events)
    window_entries = _build_window_entries(
        events_for_billing, config.window_gap_secs, config
    )
    sweep_results = sweep(events_for_billing, config)
    best_gap_entries = _build_window_entries(
        events_for_billing, sweep_results.best_gap * 60, config
    )
    return TransformResult(
        all_entries=window_entries,
        best_gap_entries=best_gap_entries,
        sweep=sweep_results,
        raw_events=events,
    )


# ══════════════════════════════════════════════════════════════════════════════
# § 7  LOAD
# ══════════════════════════════════════════════════════════════════════════════


def _write_table(entries: list[TimeEntry], f: IO[str]) -> None:
    """Write the final chronological table emitted to CSV."""
    f.write("\n## Final Time Entries\n\n")
    cols = [
        ("REPO", 38),
        ("TASK", 5),
        ("START", 20),
        ("END", 20),
        ("DURATION", 8),
    ]
    _md_table_header(cols, f)
    total_secs = 0
    for e in sorted(entries, key=lambda x: (x.repo, x.start)):
        dur = int((e.end - e.start).total_seconds())
        total_secs += dur
        f.write(
            f"| {e.repo:<38} | {e.task_id:<5} | {_fmt_et(e.start):<20} | "
            f"{_fmt_et(e.end):<20} | {_fmt_duration(dur):<8} |\n"
        )
    f.write(
        f"| {'**TOTAL**':<38} | {'':<5} | {'':<20} | {'':<20} | "
        f"{'**' + _fmt_duration(total_secs) + '**':<8} |\n"
    )


def _entries_by_task(entries: list[TimeEntry]) -> dict[str, list[TimeEntry]]:
    """Group entries by task id."""
    by_task: dict[str, list[TimeEntry]] = {}
    for entry in entries:
        by_task.setdefault(entry.task_id, []).append(entry)
    return by_task


def _mermaid_text(text: str) -> str:
    """Sanitize display text for Mermaid Gantt labels and sections."""
    text = text.replace(":", " ").replace(",", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip() or "entry"


def _mermaid_datetime(ts: datetime) -> str:
    """Format timestamps for Mermaid Gantt using local ET clock time."""
    return ts.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _mermaid_entry_label(entry: TimeEntry) -> str:
    """Build an intentionally minimal Mermaid Gantt bar label."""
    return "."


def _write_mermaid_range_anchors(
    chart_start: datetime | None, chart_end: datetime | None, f: IO[str]
) -> None:
    """Write minimal anchors that force Mermaid to show the full date range."""
    if not chart_start or not chart_end or chart_start >= chart_end:
        return
    end_anchor_start = max(chart_start, chart_end - timedelta(minutes=1))
    f.write("    section Chart Range\n")
    f.write(
        f"    . :range_start, {_mermaid_datetime(chart_start)}, "
        f"{_mermaid_datetime(chart_start + timedelta(minutes=1))}\n"
    )
    f.write(
        f"    . :range_end, {_mermaid_datetime(end_anchor_start)}, "
        f"{_mermaid_datetime(chart_end)}\n"
    )


def _write_mermaid_workday_markers(
    chart_start: datetime | None, chart_end: datetime | None, f: IO[str]
) -> None:
    """Write vertical markers for standard working hours in the chart range."""
    if not chart_start or not chart_end or chart_start >= chart_end:
        return
    current_day = chart_start.astimezone(ET).date()
    last_day = chart_end.astimezone(ET).date()
    f.write("    %% Standard working hours: 09:00-17:30 ET\n")
    while current_day <= last_day:
        for label, hour, minute in (("start", 9, 0), ("end", 17, 30)):
            marker = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                hour,
                minute,
                tzinfo=ET,
            )
            if chart_start <= marker <= chart_end:
                marker_id = f"work_{label}_{current_day.strftime('%Y%m%d')}"
                f.write(
                    f"    Work {label} {current_day:%m-%d} : vert, {marker_id}, "
                    f"{_mermaid_datetime(marker)}, 2m\n"
                )
        current_day += timedelta(days=1)


def _write_mermaid_gantt(
    title: str,
    entries: list[TimeEntry],
    f: IO[str],
    chart_start: datetime | None = None,
    chart_end: datetime | None = None,
) -> None:
    """Write a Mermaid Gantt chart for time-entry windows grouped by task."""
    f.write(f"\n## {title}\n\n")
    f.write("```mermaid\n")
    f.write("gantt\n")
    f.write(f"    title {_mermaid_text(title)} (ET)\n")
    f.write("    dateFormat YYYY-MM-DD HH:mm\n")
    f.write("    axisFormat %m-%d %H:%M\n")
    f.write("    tickInterval 1day\n")
    _write_mermaid_range_anchors(chart_start, chart_end, f)
    _write_mermaid_workday_markers(chart_start, chart_end, f)
    entry_num = 1
    for task_id, task_entries in sorted(_entries_by_task(entries).items()):
        f.write(f"    section Task {_mermaid_text(task_id)}\n")
        ordered = sorted(task_entries, key=lambda e: (e.start, e.repo, e.pr_num))
        for entry in ordered:
            entry_id = f"entry_{entry_num:04d}"
            label = _mermaid_entry_label(entry)
            f.write(
                f"    {label} :{entry_id}, "
                f"{_mermaid_datetime(entry.start)}, {_mermaid_datetime(entry.end)}\n"
            )
            entry_num += 1
    f.write("```\n")


def _rle_sweep_rows(
    totals: list[float],
    gap_vals: list[int],
    num_days: int,
    config: Config,
) -> list[str]:
    """Run-length encode identical sweep totals into formatted table row strings."""
    rows: list[str] = []
    i, n = 0, len(totals)
    while i < n:
        run_val = totals[i]
        j = i
        while j < n and totals[j] == run_val:
            j += 1
        g_start, g_end = gap_vals[i], gap_vals[j - 1]
        gap_range = f"{g_start}m" if i == j - 1 else f"{g_start}–{g_end}m"
        s = score_gap(run_val, num_days, config)
        rows.append(
            f"| {gap_range:<12} | {s:<8.3f} | {_fmt_duration(int(run_val)):<12} | {j - i:<8} |"
        )
        i = j
    return rows


def _write_sweep_tables(results: SweepResults, config: Config, f: IO[str]) -> None:
    """Write per-task sweep tables with run-length encoding of identical totals."""
    step = results.gap_vals[1] - results.gap_vals[0] if len(results.gap_vals) > 1 else 0
    f.write(
        f"\n## Sweep ({results.gap_vals[0]}–{results.gap_vals[-1]} min, {step}-min steps)\n"
    )
    cols = [("GAP RANGE", 12), ("SCORE", 8), ("TOTAL", 12), ("N_GAPS", 8)]
    for task_id in sorted(results.per_task.keys()):
        f.write(f"\n### Task {task_id}\n\n")
        _md_table_header(cols, f)
        for row in _rle_sweep_rows(
            results.per_task[task_id], results.gap_vals, results.num_days, config
        ):
            f.write(row + "\n")


def _write_sweep_summary(results: SweepResults, config: Config, f: IO[str]) -> None:
    """Write the sweep summary metrics table."""
    target_secs = config.b_low * 3600 * config.num_days
    best_dist = results.best_total - target_secs
    excluded = _fmt_excluded_target_dates(config)
    f.write("\n## Sweep Summary\n\n")
    cols = [("METRIC", 14), ("VALUE", 30)]
    _md_table_header(cols, f)
    f.write(f"| {'obs mean':<14} | {_fmt_duration(int(results.obs_mean)):<30} |\n")
    f.write(
        f"| {'target':<14} | {_fmt_duration(int(target_secs))} ({config.b_low}h/day × {config.num_days}d{excluded})  |\n"
    )
    f.write(f"| {'best gap':<14} | {results.best_gap}m{'':<24} |\n")
    f.write(f"| {'best total':<14} | {_fmt_duration(int(results.best_total)):<30} |\n")
    f.write(f"| {'best score':<14} | {results.best_score:<30.4f} |\n")
    f.write(f"| {'best Δ':<14} | {_fmt_delta(best_dist):<30} |\n")


def _entry_secs(entry: TimeEntry) -> int:
    """Return a TimeEntry duration in whole seconds."""
    return int((entry.end - entry.start).total_seconds())


def _fmt_excluded_target_dates(config: Config) -> str:
    """Return target exclusion text for report summaries."""
    excluded = sorted(
        day
        for day in config.target_excluded_dates
        if config.start_date <= day <= config.end_date
    )
    if not excluded:
        return ""
    dates = ", ".join(day.isoformat() for day in excluded)
    return f", excludes {dates}"


def _audit_warning_rows(
    result: TransformResult, config: Config
) -> list[tuple[str, str]]:
    """Build audit warning rows for suspicious generated billing output."""
    rows: list[tuple[str, str]] = []
    upper_secs = config.b_high * 3600
    over_days = {
        day: total
        for day, total in _target_day_totals(result.best_gap_entries, config).items()
        if total > upper_secs
    }
    if over_days:
        details = ", ".join(
            f"{day.isoformat()} {_fmt_duration(int(total))}"
            for day, total in sorted(over_days.items())
        )
        rows.append(
            (
                "over upper bound",
                f"{details} > {_fmt_duration(int(upper_secs))}/day "
                f"({config.b_high}h/day)",
            )
        )
    empty_task_rows = sum(
        1 for entry in result.best_gap_entries if not _is_numeric_id(entry.task_id)
    )
    if empty_task_rows:
        rows.append(("empty CSV Task/ID rows", str(empty_task_rows)))
    return rows


def _write_audit_warnings(result: TransformResult, config: Config, f: IO[str]) -> None:
    """Write audit warnings for high-risk generated output conditions."""
    rows = _audit_warning_rows(result, config)
    if not rows:
        return
    f.write("\n## Audit Warnings\n\n")
    cols = [("WARNING", 24), ("DETAIL", 56)]
    _md_table_header(cols, f)
    for warning, detail in rows:
        f.write(f"| {warning:<24} | {detail:<56} |\n")
        print(f"[audit] {warning}: {detail}", file=sys.stderr)


def _unknown_events(events: list[RawEvent]) -> list[RawEvent]:
    """Return unresolved source events, sorted for the final audit table."""
    return sorted(
        [
            event
            for event in events
            if not event.task_ids or "UNKNOWN" in event.task_ids
        ],
        key=lambda event: (event.timestamp, event.repo, event.pr_num, event.branch),
    )


def _unknown_source_detail(events: list[RawEvent]) -> tuple[str, str, str]:
    """Return source, branch, and event timestamp detail for unresolved events."""
    if not events:
        return "unmatched", "", ""
    sources = sorted(
        {f"PR #{event.pr_num}" if event.pr_num else "local git" for event in events}
    )
    branches = sorted({event.branch or "(empty)" for event in events})
    event_bits = [
        f"{event.event_type.name.lower()} {_fmt_et(event.timestamp)}"
        for event in sorted(events, key=lambda event: event.timestamp)
    ]
    return ", ".join(sources), ", ".join(branches), "; ".join(event_bits)


def _write_unknown_sources(result: TransformResult, f: IO[str]) -> None:
    """Write unresolved raw source events excluded from billing output."""
    unknown_events = _unknown_events(result.raw_events)
    f.write("\n## Unresolved Task Sources\n\n")
    if not unknown_events:
        f.write("No UNKNOWN source events were found.\n")
        return
    cols = [
        ("EVENT TIME", 20),
        ("REPO", 32),
        ("SOURCE", 14),
        ("BRANCH", 28),
        ("EVENT", 8),
        ("STATUS", 30),
    ]
    _md_table_header(cols, f)
    for event in unknown_events:
        source, branch, _event_detail = _unknown_source_detail([event])
        f.write(
            f"| {_fmt_et(event.timestamp):<20} | {event.repo:<32} | "
            f"{source:<14} | {branch:<28} | "
            f"{event.event_type.name.lower():<8} | excluded from billing output   |\n"
        )


_CSV_COLUMNS = [
    "Date",
    "Description",
    "Project/ID",
    "Task/ID",
    "Quantity",
    "Employee/ID",
    "Unit of Measure/ID",
    "Company/ID",
    "Sales Order Item/ID",
]


def _is_numeric_id(s: str) -> bool:
    """Return True if s is a non-empty string of digits."""
    return bool(s) and s.isdigit()


def _description_word_limit(entry: TimeEntry) -> int:
    """Return the dynamic maximum word count for one CSV description."""
    _ = entry
    return 12


def _bounded_context(text: str, max_chars: int = 700) -> str:
    """Return compact one-line context for a Claude prompt."""
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:max_chars]


def _business_context(text: str, max_chars: int = 220) -> str:
    """Return customer-facing context with technical identifiers removed."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(?:PR|pull request)\s*#?\d+\b", " ", text, flags=re.I)
    text = re.sub(r"#\d+\b", " ", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", " ", text, flags=re.I)
    text = re.sub(r"\b[\w.-]+/[\w.-]+\b", " ", text)
    text = re.sub(
        r"\b(?:branch|commit|merge|review|ticket|issue|task|id)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b\d{4,}\b", " ", text)
    return _bounded_context(text, max_chars)


def _description_context_lines(entry: TimeEntry) -> list[str]:
    """Return non-technical business context lines for one TimeEntry."""
    settings = _strategy_settings_for_entry(entry)
    return _strategy_context_lines(entry, settings)


def _strategy_settings_for_entry(entry: TimeEntry) -> SessionStrategyConfig:
    """Return configured strategy settings for a TimeEntry."""
    for settings in DEFAULT_SESSION_STRATEGY_CONFIGS:
        if settings.name == entry.strategy_name:
            return settings
    return DEFAULT_SESSION_STRATEGY_CONFIGS[0]


def _strategy_context_lines(
    entry: TimeEntry, settings: SessionStrategyConfig
) -> list[str]:
    """Return scrubbed context lines using configured event fields."""
    lines: list[str] = []
    for event in entry.source_events:
        for field_name in settings.context_fields:
            context = _business_context(str(getattr(event, field_name, "")))
            if context:
                lines.append(context)
                break
    return lines or [settings.fallback_action]


def _entry_description_prompt(entry: TimeEntry) -> str:
    """Build the Claude prompt for one CSV row without the AI prefix marker."""
    context_lines = "\n".join(f"- {line}" for line in _description_context_lines(entry))
    return (
        "Write only the action phrase for a customer-facing timesheet row.\n"
        f"Category: {entry.strategy_category}\n"
        "Use terse direct action-to-outcome wording with zero fluff.\n"
        "No markdown. No double quotes. Avoid commas. Return only the description.\n"
        "Do not mention internal references identifiers timing or implementation mechanics.\n"
        f"Use at most {_description_word_limit(entry)} words.\n"
        f"Business context:\n{context_lines}"
    )


def _entry_default_description(entry: TimeEntry) -> str:
    """Return deterministic strategy-based fallback description."""
    settings = _strategy_settings_for_entry(entry)
    action = _sanitize_description(settings.fallback_action)
    return f"{AI_DESCRIPTION_PREFIX} {settings.category}: {action}"


def _entry_prefixed_description(entry: TimeEntry, action: str) -> str:
    """Return the final prefixed strategy/category description."""
    return f"{AI_DESCRIPTION_PREFIX} {entry.strategy_category}: {action}"


def _call_claude_description(prompt: str) -> str:
    """Call Claude CLI for a CSV description; return stdout or empty string."""
    if not _claude_available():
        return ""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku", "--effort", "low"],
            capture_output=True,
            text=True,
            timeout=CLAUDE_DESCRIPTION_TIMEOUT_SECS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _claude_available() -> bool:
    """Return whether the Claude CLI is available on PATH."""
    return shutil.which("claude") is not None


def _description_log_prefix(row_index: int | None, row_total: int | None) -> str:
    """Return a stable log prefix for one description generation attempt."""
    if row_index is None or row_total is None:
        return "[csv description]"
    return f"[csv description {row_index}/{row_total}]"


def _sanitize_description(text: str) -> str:
    """Return CSV-safe Claude description text without the AI prefix."""
    text = text.strip()
    if text.startswith(AI_DESCRIPTION_PREFIX):
        text = text[len(AI_DESCRIPTION_PREFIX) :].strip()
    text = _business_context(text, 300)
    text = re.sub(r'["\r\n,;]+', " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _entry_description(
    entry: TimeEntry,
    config: Config,
    row_index: int | None = None,
    row_total: int | None = None,
) -> str:
    """Return a CSV description for one TimeEntry."""
    _ = config
    prompt = _entry_description_prompt(entry)
    prefix = _description_log_prefix(row_index, row_total)
    if not _claude_available():
        print(f"{prefix} Claude not found; fallback", file=sys.stderr)
        return _entry_default_description(entry)
    print(
        f"{prefix} calling Claude for {entry.repo} task={entry.task_id} "
        f"events={len(entry.source_events)} timeout={CLAUDE_DESCRIPTION_TIMEOUT_SECS}s",
        file=sys.stderr,
    )
    started = time.monotonic()
    text = _sanitize_description(_call_claude_description(prompt))
    elapsed = time.monotonic() - started
    if not text or len(text.split()) > _description_word_limit(entry):
        print(f"{prefix} fallback after {elapsed:.1f}s", file=sys.stderr)
        return _entry_default_description(entry)
    print(f"{prefix} generated after {elapsed:.1f}s", file=sys.stderr)
    return _entry_prefixed_description(entry, text)


def _entry_to_csv_row(
    entry: TimeEntry,
    config: Config,
    row_index: int | None = None,
    row_total: int | None = None,
) -> dict:
    """Convert one TimeEntry to an Odoo-importable CSV row dict."""
    qty = (entry.end - entry.start).total_seconds() / 3600.0
    task_id = entry.task_id if _is_numeric_id(entry.task_id) else ""
    return {
        "Date": entry.start.astimezone(ET).strftime("%Y-%m-%d"),
        "Description": _entry_description(entry, config, row_index, row_total),
        "Project/ID": entry.repo,
        "Task/ID": task_id,
        "Quantity": round(qty, 10),
        "Employee/ID": config.odoo_employee_id,
        "Unit of Measure/ID": config.odoo_uom_id,
        "Company/ID": config.odoo_company_id,
        "Sales Order Item/ID": "",
    }


def _fallback_csv_row(entry: TimeEntry, config: Config) -> dict:
    """Return a CSV row with the deterministic fallback description."""
    qty = (entry.end - entry.start).total_seconds() / 3600.0
    task_id = entry.task_id if _is_numeric_id(entry.task_id) else ""
    return {
        "Date": entry.start.astimezone(ET).strftime("%Y-%m-%d"),
        "Description": _entry_default_description(entry),
        "Project/ID": entry.repo,
        "Task/ID": task_id,
        "Quantity": round(qty, 10),
        "Employee/ID": config.odoo_employee_id,
        "Unit of Measure/ID": config.odoo_uom_id,
        "Company/ID": config.odoo_company_id,
        "Sales Order Item/ID": "",
    }


def _csv_row_worker(args: tuple[int, TimeEntry, Config, int]) -> tuple[int, dict]:
    """Build one CSV row for use with ThreadPoolExecutor."""
    index, entry, config, total = args
    return index, _entry_to_csv_row(entry, config, index + 1, total)


def _csv_rows_with_descriptions(entries: list[TimeEntry], config: Config) -> list[dict]:
    """Build CSV rows with bounded concurrent Claude description calls."""
    total = len(entries)
    rows: list[dict | None] = [None] * total
    work = [(index, entry, config, total) for index, entry in enumerate(entries)]
    with ThreadPoolExecutor(max_workers=CLAUDE_DESCRIPTION_CONCURRENCY) as executor:
        futures = {executor.submit(_csv_row_worker, item): item for item in work}
        for future in as_completed(futures):
            index, entry, _config, _total = futures[future]
            try:
                row_index, row = future.result()
                rows[row_index] = row
            except Exception as exc:
                prefix = _description_log_prefix(index + 1, total)
                print(f"{prefix} worker error: {exc}; fallback", file=sys.stderr)
                rows[index] = _fallback_csv_row(entry, config)
    return [row for row in rows if row is not None]


def _write_odoo_csv(result: TransformResult, config: Config) -> None:
    """Write an Odoo-importable timesheet CSV from best-gap time entries."""
    total = len(result.best_gap_entries)
    worst_case_secs = total * CLAUDE_DESCRIPTION_TIMEOUT_SECS
    print(
        f"Phase 4: Writing CSV with Claude descriptions for {total} final rows "
        f"(not sweep iterations; concurrency={CLAUDE_DESCRIPTION_CONCURRENCY}; "
        f"timeout budget up to {worst_case_secs}s sequential)...",
        file=sys.stderr,
    )
    rows = _csv_rows_with_descriptions(result.best_gap_entries, config)
    if not rows:
        return
    with open(config.odoo_csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written to {config.odoo_csv_file}", file=sys.stderr)


def _write_markdown(result: TransformResult, config: Config) -> None:
    """Write the full analysis report to the markdown outfile."""
    generated = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    with open(config.outfile, "a", encoding="utf-8") as f:
        _write_sweep_tables(result.sweep, config, f)
        _write_sweep_summary(result.sweep, config, f)
        _write_audit_warnings(result, config, f)
        _write_table(result.best_gap_entries, f)
        _write_mermaid_gantt(
            f"Final Window Diagram (gap = {result.sweep.best_gap}m)",
            result.best_gap_entries,
            f,
            chart_start=config.range_start,
            chart_end=config.range_end - timedelta(minutes=1),
        )
        _write_unknown_sources(result, f)
        f.write(f"\n_Generated {generated}_\n")


def load(result: TransformResult, config: Config) -> None:
    """L in ETL: write all output sinks."""
    _write_markdown(result, config)
    if config.odoo_csv_file:
        _write_odoo_csv(result, config)


# ══════════════════════════════════════════════════════════════════════════════

# § 8  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


def _make_config(**overrides) -> Config:
    """Construct a Config from globals, optionally overriding fields."""
    local_events_cache_file = overrides.pop(
        "local_events_cache_file", LOCAL_EVENTS_CACHE_FILE
    )
    local_repos_path = overrides.pop("local_repos_path", LOCAL_REPOS_PATH)
    values = {
        "gh_user": GH_USER,
        "org": ORG,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "target_excluded_dates": set(TARGET_EXCLUDED_DATES),
        "outfile": OUTFILE,
        "window_gap_secs": WINDOW_GAP_SECS,
        "min_task_minutes": MIN_TASK_MINUTES,
        "billing_step_mins": BILLING_STEP_MINS,
        "sweep_min_gap_mins": SWEEP_MIN_GAP_MINS,
        "sweep_max_gap_mins": SWEEP_MAX_GAP_MINS,
        "sweep_step_mins": SWEEP_STEP_MINS,
        "local_repos_path": local_repos_path,
        "local_events_cache_file": local_events_cache_file,
        "odoo_csv_file": ODOO_CSV_FILE,
        "odoo_employee_id": ODOO_EMPLOYEE_ID,
        "odoo_uom_id": ODOO_UOM_ID,
        "odoo_company_id": ODOO_COMPANY_ID,
        "b_low": B_LOW,
        "b_high": B_HIGH,
        "s_low": S_LOW,
        "s_high": S_HIGH,
        "k1": K1,
        "k2": K2,
        "k3": K3,
    }
    values.update(overrides)
    return Config(**values)


def main() -> None:
    """Run the full ETL pipeline and write timelog.md."""
    config = _make_config()
    config.diag = DiagnosticWriter(config.outfile, config.start_date, config.end_date)
    events = extract(config)
    result = transform(events, config)
    config.diag.close_code_block()
    config.diag.close()
    load(result, config)
    print(f"Written to {config.outfile}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# § 9  TUNER  (python3 timelog.py --tune)
# ══════════════════════════════════════════════════════════════════════════════


TUNABLE_CONFIG_FIELDS = (
    "window_gap_secs",
    "min_task_minutes",
    "billing_step_mins",
    "sweep_min_gap_mins",
    "sweep_max_gap_mins",
    "sweep_step_mins",
    "b_low",
    "b_high",
    "s_low",
    "s_high",
    "k1",
    "k2",
    "k3",
)

VALID_STRATEGY_GROUP_KEYS = (
    "strategy",
    "repo",
    "task_id",
    "event_type",
    "pr_num",
    "timestamp",
)


@dataclass
class TuningCandidate:
    """One manually-reviewed tuning candidate."""

    candidate_id: str
    generation: int
    payload: dict
    mutation_notes: list[str]
    summary: dict = field(default_factory=dict)


def _strategy_config_to_dict(settings: SessionStrategyConfig) -> dict:
    """Serialize one strategy settings row to JSON-safe data."""
    data = {name: getattr(settings, name) for name in settings.__dataclass_fields__}
    data["event_types"] = [event_type.name for event_type in settings.event_types]
    data["group_keys"] = list(settings.group_keys)
    data["context_fields"] = list(settings.context_fields)
    return data


def _strategy_config_from_dict(data: dict) -> SessionStrategyConfig:
    """Deserialize one strategy settings row from JSON-safe data."""
    values = dict(data)
    values["event_types"] = tuple(EventType[name] for name in values["event_types"])
    values["group_keys"] = tuple(values["group_keys"])
    values["context_fields"] = tuple(values["context_fields"])
    return SessionStrategyConfig(**values)


def _default_tuning_payload() -> dict:
    """Return JSON-safe tunable config from current code defaults."""
    config = _make_config()
    return {
        "config": {name: getattr(config, name) for name in TUNABLE_CONFIG_FIELDS},
        "strategies": [
            _strategy_config_to_dict(settings)
            for settings in DEFAULT_SESSION_STRATEGY_CONFIGS
        ],
    }


def _tuning_config_path() -> Path:
    """Return the persisted tuner winner path."""
    return Path(TUNING_CONFIG_FILE)


def _load_tuning_payload() -> dict:
    """Load persisted tuner config, or return defaults if none exists."""
    path = _tuning_config_path()
    if not path.exists():
        return _default_tuning_payload()
    return json.loads(path.read_text(encoding="utf-8"))


def _write_tuning_payload(payload: dict) -> None:
    """Persist the selected winner config to disk."""
    path = _tuning_config_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Persisted winner to {path}")


def _config_from_tuning_payload(payload: dict) -> Config:
    """Build a Config from tunable payload values only."""
    strategies = tuple(_strategy_config_from_dict(row) for row in payload["strategies"])
    config = _make_config(**payload["config"], session_strategy_configs=strategies)
    _make_sessionization_context(config.session_strategy_configs)
    _validate_tuning_config(config)
    return config


def _validate_tuning_config(config: Config) -> None:
    """Validate tuner invariants that Config cannot fully express."""
    if config.sweep_min_gap_mins > config.sweep_max_gap_mins:
        raise ValueError("sweep_min_gap_mins must be <= sweep_max_gap_mins")
    if config.b_low >= config.b_high:
        raise ValueError("b_low must be less than b_high")
    if config.s_low >= config.s_high:
        raise ValueError("s_low must be less than s_high")
    if min(config.k1, config.k2, config.k3) <= 0:
        raise ValueError("score coefficients must be positive")
    for settings in config.session_strategy_configs:
        _validate_strategy_settings(settings)


def _validate_strategy_settings(settings: SessionStrategyConfig) -> None:
    """Validate one strategy settings row for tuner use."""
    if settings.fixed_secs <= 0 or settings.gap_secs <= 0:
        raise ValueError("strategy durations must be positive")
    if settings.billing_floor_secs <= 0 or settings.billing_step_secs <= 0:
        raise ValueError("strategy billing values must be positive")
    unknown = set(settings.group_keys) - set(VALID_STRATEGY_GROUP_KEYS)
    if unknown:
        raise ValueError(f"unknown group keys: {sorted(unknown)}")


def _mutate_int(value: int, rng: random.Random, step: int, low: int) -> int:
    """Return a bounded integer mutation."""
    return max(low, value + rng.choice([-step, step]))


def _mutate_float(value: float, rng: random.Random, step: float, low: float) -> float:
    """Return a bounded rounded float mutation."""
    return round(max(low, value + rng.choice([-step, step])), 4)


def _mutate_config_values(values: dict, rng: random.Random) -> tuple[dict, str]:
    """Mutate one tunable scalar config value."""
    values = dict(values)
    field_name = rng.choice(tuple(values))
    if isinstance(values[field_name], int):
        values[field_name] = _mutate_int(values[field_name], rng, 5, 1)
    else:
        values[field_name] = _mutate_float(values[field_name], rng, 0.25, 0.001)
    if values["sweep_min_gap_mins"] < 2 * values["min_task_minutes"]:
        values["sweep_min_gap_mins"] = 2 * values["min_task_minutes"]
    if values["sweep_max_gap_mins"] < values["sweep_min_gap_mins"]:
        values["sweep_max_gap_mins"] = values["sweep_min_gap_mins"]
    if values["b_low"] >= values["b_high"]:
        values["b_high"] = values["b_low"] + 0.25
    if values["s_low"] >= values["s_high"]:
        values["s_high"] = values["s_low"] + 0.25
    return values, f"mutated config.{field_name}"


def _mutate_strategy_rows(
    rows: list[dict], rng: random.Random
) -> tuple[list[dict], str]:
    """Mutate one flat strategy settings row."""
    rows = [dict(row) for row in rows]
    if rng.random() < 0.25:
        return _mutate_event_assignment(rows, rng)
    row = rng.choice(rows)
    field_name = rng.choice(("gap_secs", "fixed_secs", "billing_floor_secs"))
    row[field_name] = _mutate_int(int(row[field_name]), rng, 300, 300)
    return rows, f"mutated strategy.{row['name']}.{field_name}"


def _mutate_event_assignment(
    rows: list[dict], rng: random.Random
) -> tuple[list[dict], str]:
    """Move one EventType assignment to another strategy row."""
    event_name = rng.choice([event_type.name for event_type in EventType])
    for row in rows:
        row["event_types"] = [name for name in row["event_types"] if name != event_name]
    target = rng.choice(rows)
    target["event_types"] = sorted(set(target["event_types"] + [event_name]))
    return rows, f"assigned {event_name} to strategy.{target['name']}"


def _mutated_candidate(
    base: dict, generation: int, index: int, rng: random.Random
) -> TuningCandidate:
    """Return one mutated candidate payload."""
    payload = json.loads(json.dumps(base))
    if rng.random() < 0.5:
        payload["config"], note = _mutate_config_values(payload["config"], rng)
    else:
        payload["strategies"], note = _mutate_strategy_rows(payload["strategies"], rng)
    return TuningCandidate(f"g{generation}c{index}", generation, payload, [note])


def _candidate_population(base: dict, generation: int) -> list[TuningCandidate]:
    """Return baseline plus a small mutated population for one generation."""
    rng = random.Random(generation)
    baseline = TuningCandidate(f"g{generation}c0", generation, base, ["current winner"])
    return [baseline] + [
        _mutated_candidate(base, generation, i, rng) for i in range(1, 5)
    ]


def _evaluate_candidate(candidate: TuningCandidate, events: list[RawEvent]) -> None:
    """Populate one candidate's terminal summary without writing output files."""
    config = _config_from_tuning_payload(candidate.payload)
    result = transform(events, config)
    candidate.summary = _tuning_summary(result, config)


def _tuning_summary(result: TransformResult, config: Config) -> dict:
    """Return a compact summary for manual candidate review."""
    entries = result.best_gap_entries
    total_secs = sum((entry.end - entry.start).total_seconds() for entry in entries)
    strategy_totals = _strategy_hour_totals(entries)
    warnings = _audit_warning_rows(result, config)
    return {
        "rows": len(entries),
        "hours": round(total_secs / 3600.0, 2),
        "by_strategy": strategy_totals,
        "non_quarter": _non_quarter_entry_count(entries),
        "empty_tasks": sum(1 for entry in entries if not _is_numeric_id(entry.task_id)),
        "warnings": len(warnings),
        "best_gap": result.sweep.best_gap,
        "best_score": round(result.sweep.best_score, 4),
    }


def _strategy_hour_totals(entries: list[TimeEntry]) -> dict[str, float]:
    """Return billed hours by strategy category."""
    totals: dict[str, float] = {}
    for entry in entries:
        hours = (entry.end - entry.start).total_seconds() / 3600.0
        totals[entry.strategy_category] = (
            totals.get(entry.strategy_category, 0.0) + hours
        )
    return {name: round(hours, 2) for name, hours in sorted(totals.items())}


def _non_quarter_entry_count(entries: list[TimeEntry]) -> int:
    """Return count of entries whose duration is not a quarter-hour multiple."""
    return sum(
        1
        for entry in entries
        if int((entry.end - entry.start).total_seconds()) % (15 * 60) != 0
    )


def _print_candidate(candidate: TuningCandidate) -> None:
    """Print one candidate summary for manual review."""
    summary = candidate.summary
    print(
        f"{candidate.candidate_id}: rows={summary['rows']} hours={summary['hours']} "
        f"best_gap={summary['best_gap']} score={summary['best_score']} "
        f"non_quarter={summary['non_quarter']} empty_tasks={summary['empty_tasks']} "
        f"warnings={summary['warnings']} by_strategy={summary['by_strategy']} "
        f"notes={'; '.join(candidate.mutation_notes)}"
    )


def _tuner_events(source: str, config: Config) -> list[RawEvent] | None:
    """Return reusable events for one tuning session, or None if unsupported."""
    if source == "1":
        return extract(config)
    if source == "2":
        return _synthetic_tuner_events(config)
    print("Existing generated outputs are not supported for tuning yet.")
    return None


def _synthetic_tuner_events(config: Config) -> list[RawEvent]:
    """Return a small deterministic event set for tuner smoke testing."""
    timestamp = config.range_start.astimezone(timezone.utc)
    repo = "QOC-Innovations/tuner-fixture"
    return [
        RawEvent(timestamp, ["10001"], repo, 1, EventType.COMMIT),
        RawEvent(
            timestamp + timedelta(minutes=45), ["10001"], repo, 1, EventType.COMMIT
        ),
        RawEvent(timestamp + timedelta(hours=2), ["10002"], repo, 2, EventType.REVIEW),
        RawEvent(timestamp + timedelta(hours=3), ["10003"], repo, 3, EventType.MERGE),
    ]


def _prompt_tuner_source() -> str:
    """Ask which data source to use for this tuning run."""
    print(
        "Tuning data source: 1=current extracted data, 2=synthetic fixture, 3=existing outputs"
    )
    return input("Choose source [1/2/3]: ").strip() or "1"


def _run_tuner() -> None:
    """Run the interactive manual strategy/config tuner."""
    payload = _load_tuning_payload()
    config = _config_from_tuning_payload(payload)
    events = _tuner_events(_prompt_tuner_source(), config)
    if events is None:
        return
    generation = 1
    while True:
        population = _candidate_population(payload, generation)
        for candidate in population:
            _evaluate_candidate(candidate, events)
            _print_candidate(candidate)
        answer = input("Winner id, k=kill/regenerate, q=quit: ").strip()
        if answer.lower() == "q":
            return
        winner = next(
            (item for item in population if item.candidate_id == answer), None
        )
        if winner is not None:
            payload = winner.payload
            _write_tuning_payload(payload)
            generation += 1
        elif answer.lower() != "k":
            print("Unknown selection; keeping previous winner.")


# ══════════════════════════════════════════════════════════════════════════════
# § 10  FUZZER  (python3 timelog.py --fuzz)
# ══════════════════════════════════════════════════════════════════════════════


_EPS = 1e-9


def _assert(condition: bool, msg: str) -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {msg}")
    return condition


def _make_synthetic_pr(
    pr_idx: int, rng: random.Random, range_secs: int, config: Config
) -> list[RawEvent]:
    """Generate synthetic commit + optional merge events for one fuzzer PR."""
    task_ids = [str(rng.randint(10000, 99999)) for _ in range(rng.randint(1, 3))]
    repo = f"QOC-Innovations/fuzz-repo-{pr_idx}"
    pr_num = pr_idx + 1
    branch = f"{task_ids[0]}#fuzz"
    is_rel = len(task_ids) > 1
    events: list[RawEvent] = []
    for _ in range(rng.randint(1, 6)):
        offset = rng.randint(0, range_secs - 1)
        ts = (config.range_start + timedelta(seconds=offset)).astimezone(timezone.utc)
        events.append(
            RawEvent(
                timestamp=ts,
                task_ids=task_ids,
                repo=repo,
                pr_num=pr_num,
                event_type=EventType.COMMIT,
                branch=branch,
                is_release=is_rel,
            )
        )
    if rng.random() > 0.3:
        offset = rng.randint(0, range_secs - 1)
        ts = (config.range_start + timedelta(seconds=offset)).astimezone(timezone.utc)
        events.append(
            RawEvent(
                timestamp=ts,
                task_ids=task_ids,
                repo=repo,
                pr_num=pr_num,
                event_type=EventType.MERGE,
                branch=branch,
                is_release=is_rel,
            )
        )
    return events


def _generate_events(seed: int, config: Config) -> list[RawEvent]:
    """Generate a deterministic list of synthetic RawEvents for fuzzing."""
    rng = random.Random(seed)
    range_secs = int((config.range_end - config.range_start).total_seconds())
    if range_secs <= 0:
        return []
    return [
        ev
        for pr_idx in range(rng.randint(1, 8))
        for ev in _make_synthetic_pr(pr_idx, rng, range_secs, config)
    ]


def _monotonicity_seed(
    seed: int, gap_vals: list[int], config: Config
) -> tuple[list[str], list[str]]:
    """Compute monotonicity violations for one fuzz seed across all gap values."""
    events = _generate_events(seed, config)
    groups = _group_events_by_repo_task(events)
    counts = [
        sum(
            len(compute_windows([event.timestamp for event in group], g * 60, config))
            for group in groups.values()
        )
        for g in gap_vals
    ]
    totals = [_sweep_gap(events, g, config)[1] for g in gap_vals]
    count_v = [
        f"{gap_vals[i]}m→{gap_vals[i+1]}m: {counts[i]}<{counts[i+1]}"
        for i in range(len(counts) - 1)
        if counts[i] < counts[i + 1]
    ]
    total_v = [
        f"{gap_vals[i]}m→{gap_vals[i+1]}m: {totals[i]:.0f}>{totals[i+1]:.0f}"
        for i in range(len(totals) - 1)
        if totals[i] > totals[i + 1] + 1
    ]
    return count_v, total_v


def _fuzz_sweep_monotonicity(config: Config) -> bool:
    """[3] Verify sweep satisfies both physical constraints.

    (a) Window count is monotonically NON-INCREASING as gap grows:
        larger gaps can only merge sessions, never split them.

    (b) Total billed time is monotonically NON-DECREASING as gap grows:
        every merge adds the bridging gap interval to the total
        (session_duration = n * min_task_secs + span).

    Both invariants hold simultaneously under the additive duration model
    in compute_windows.
    """
    print(
        "\n[3] Sweep physical constraints (non-increasing count, non-decreasing total)"
    )
    ok = True
    gap_vals = list(
        range(
            config.sweep_min_gap_mins,
            config.sweep_max_gap_mins + 1,
            config.sweep_step_mins,
        )
    )
    for seed in range(20):
        count_v, total_v = _monotonicity_seed(seed, gap_vals, config)
        ok &= _assert(
            not count_v,
            f"seed={seed}: count non-increasing" + (f" {count_v}" if count_v else ""),
        )
        ok &= _assert(
            not total_v,
            f"seed={seed}: total non-decreasing" + (f" {total_v}" if total_v else ""),
        )
    return ok


def _fuzz_score_optimal(config: Config) -> bool:
    """Check that scores in the optimal zone are strictly between S_LOW and S_HIGH."""
    grid = [config.b_low + (config.b_high - config.b_low) * t / 4 for t in range(1, 4)]
    scores = [score_gap(h * 3600, 1, config) for h in grid]
    ok = _assert(
        all(s > config.s_low for s in scores),
        f"Interior optimal > S_LOW ({[f'{s:.3f}' for s in scores]})",
    )
    ok &= _assert(
        all(s < config.s_high for s in scores),
        f"Interior optimal < S_HIGH ({[f'{s:.3f}' for s in scores]})",
    )
    return ok


def _fuzz_score_invariants(config: Config) -> bool:
    """[1] Verify score_gap satisfies all mathematical invariants."""
    print("\n[1] Score function invariants")
    ok = True
    eps_h = 1e-6
    s_low = score_gap(config.b_low * 3600, 1, config)
    s_high = score_gap(config.b_high * 3600, 1, config)
    ok &= _assert(
        abs(s_low - config.s_low) < _EPS,
        f"f(B_LOW={config.b_low}) == S_LOW={config.s_low} (got {s_low:.10f})",
    )
    ok &= _assert(
        abs(s_high - config.s_high) < _EPS,
        f"f(B_HIGH={config.b_high}) == S_HIGH={config.s_high} (got {s_high:.10f})",
    )
    for label, x, limit in [
        ("B_LOW−ε", config.b_low - eps_h, config.s_low),
        ("B_LOW+ε", config.b_low + eps_h, config.s_low),
        ("B_HIGH−ε", config.b_high - eps_h, config.s_high),
        ("B_HIGH+ε", config.b_high + eps_h, config.s_high),
    ]:
        s = score_gap(x * 3600, 1, config)
        ok &= _assert(abs(s - limit) < 0.01, f"f({label}) ≈ {limit} (got {s:.6f})")
    grid_under = [config.b_low - i * 0.5 for i in range(1, 10)]
    scores_u = [score_gap(h * 3600, 1, config) for h in grid_under]
    ok &= _assert(
        all(scores_u[i] <= scores_u[i - 1] for i in range(1, len(scores_u))),
        f"Underutilisation monotonically non-increasing ({[f'{s:.3f}' for s in scores_u]})",
    )
    grid_over = [config.b_high + i * 0.5 for i in range(1, 10)]
    scores_o = [score_gap(h * 3600, 1, config) for h in grid_over]
    ok &= _assert(
        all(scores_o[i] <= scores_o[i - 1] for i in range(1, len(scores_o))),
        f"Dishonesty region monotonically non-increasing ({[f'{s:.3f}' for s in scores_o]})",
    )
    ok &= _fuzz_score_optimal(config)
    return ok


def _fuzz_crash_free_sweep(config: Config) -> bool:
    """[2] Verify transform() doesn't crash for 100 deterministic event sets."""
    print("\n[2] Crash-free sweep (100 deterministic seeds)")
    n_seeds, passed = 100, 0
    for seed in range(n_seeds):
        try:
            events = _generate_events(seed, config)
            with open(os.devnull, "w") as devnull:
                old_stderr, sys.stderr = sys.stderr, devnull
                try:
                    result = transform(events, config)
                finally:
                    sys.stderr = old_stderr
            ok = (
                config.sweep_min_gap_mins
                <= result.sweep.best_gap
                <= config.sweep_max_gap_mins
                and result.sweep.best_total >= 0
            )
            if ok:
                passed += 1
        except Exception as exc:
            print(f"    FAIL seed={seed}: {exc}")
    return _assert(
        passed == n_seeds, f"{passed}/{n_seeds} seeds passed crash-free sweep"
    )


def _fuzz_multi_task_window_split(config: Config) -> bool:
    """Verify commit-window time is split across every extracted task."""
    print("\n[4] Multi-task commit-window attribution")
    timestamp = config.range_start.astimezone(timezone.utc)
    events = [
        RawEvent(
            timestamp=timestamp,
            task_ids=["100", "200", "300"],
            repo="QOC-Innovations/fuzz-repo",
            pr_num=1,
            event_type=EventType.COMMIT,
            branch="100-fuzz",
            is_release=True,
        )
    ]
    entries = _build_window_entries(events, config.window_gap_secs, config)
    durations = {
        entry.task_id: int((entry.end - entry.start).total_seconds())
        for entry in entries
    }
    ok = _assert(set(durations) == {"100", "200", "300"}, "all tasks receive time")
    ok &= _assert(
        all(seconds == config.min_task_secs for seconds in durations.values()),
        f"each task receives a task window ({durations})",
    )
    return ok


def _fuzz_daily_score_best_gap(config: Config) -> bool:
    """Verify best-gap selection prefers balanced daily scores."""
    print("\n[5] Daily score best-gap selection")
    day_one = config.range_start
    day_two = config.range_start + timedelta(days=1)
    skewed = [TimeEntry("100", "repo", 0, day_one, day_one + timedelta(hours=16))]
    balanced = [
        TimeEntry("100", "repo", 0, day_one, day_one + timedelta(hours=8)),
        TimeEntry("100", "repo", 0, day_two, day_two + timedelta(hours=8)),
    ]
    scores = [
        _score_entries_by_target_day(skewed, config),
        _score_entries_by_target_day(balanced, config),
    ]
    best_idx = _find_best_gap([20, 40], [57600.0, 57600.0], scores, config)
    ok = _assert(scores[1] > scores[0], "balanced days score above skewed total")
    ok &= _assert(best_idx == 1, "best gap follows per-day score")
    return ok


def _fuzz_invalid_sweep_minimum() -> bool:
    """Verify invalid sweep minimum configuration fails fast."""
    print("\n[6] Config validation")
    try:
        Config(min_task_minutes=10, sweep_min_gap_mins=5)
    except ValueError:
        return _assert(True, "invalid sweep minimum raises ValueError")
    return _assert(False, "invalid sweep minimum raises ValueError")


def _fuzz_target_date_exclusions() -> bool:
    """Verify target exclusions do not alter event output inclusion."""
    print("\n[7] Target date exclusions")
    excluded = date(2026, 7, 3)
    config = Config(
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 3),
        target_excluded_dates={excluded},
        sweep_min_gap_mins=30,
    )
    holiday_noon = datetime(2026, 7, 3, 12, 0, tzinfo=ET)
    event = RawEvent(
        timestamp=holiday_noon,
        task_ids=["77777"],
        repo="QOC-Innovations/fuzz-repo",
        pr_num=1,
        event_type=EventType.COMMIT,
        branch="77777-fuzz",
        is_release=False,
    )
    result = transform([event], config)
    final_dates = {
        entry.start.astimezone(ET).date() for entry in result.best_gap_entries
    }
    scored_totals = _target_day_totals(result.best_gap_entries, config)
    ok = _assert(config.num_days == 2, "holiday excluded from target day count")
    ok &= _assert(config.in_range(holiday_noon), "holiday events remain in range")
    ok &= _assert(excluded in final_dates, "holiday events remain in final output")
    ok &= _assert(excluded not in scored_totals, "holiday excluded from score totals")
    ok &= _assert(
        _github_updated_range(config) == "2026-07-01T04:00:00Z..2026-07-04T03:59:59Z",
        "GitHub search covers full ET date range",
    )
    return ok


def _fuzz_repo_name_normalization() -> bool:
    """Verify GitHub remote URLs normalize to owner/repo names."""
    print("\n[8] Local repo name normalization")
    cases = {
        "git@github.com:QOC-Innovations/qocinnovations.git": "QOC-Innovations/qocinnovations",
        "https://github.com/CoreFXIngredients/CoreFX.git": "CoreFXIngredients/CoreFX",
        "https://example.com/not-github/repo.git": "",
    }
    ok = True
    for origin, expected in cases.items():
        ok &= _assert(_repo_name_from_remote(origin) == expected, origin)
    return ok


def _fuzz_local_event_cache(config: Config) -> bool:
    """Verify local-event cache bridges host/container repo availability."""
    print("\n[9] Local event cache")
    path = Path(f"/tmp/timelog_local_cache_fuzz_{os.getpid()}.json")
    cfg = _make_config(
        local_events_cache_file=str(path),
        local_repos_path="/tmp/missing-timelog-repos",
    )
    event = RawEvent(
        config.range_start,
        ["12345"],
        "owner/repo",
        0,
        EventType.COMMIT,
        "12345-work",
        subject="Fix cache",
    )
    try:
        _write_local_events_cache([event], cfg)
        events, diags = fetch_local_commits(cfg)
        ok = _assert(
            len(events) == 1 and events[0].subject == "Fix cache",
            "missing repos load cache",
        )
        ok &= _assert(
            len(diags) == 1 and diags[0].repo_dir == "owner/repo",
            "cache creates diagnostics",
        )
        mismatch = _make_config(local_events_cache_file=str(path))
        mismatch.end_date = date(2026, 7, 3)
        ok &= _assert(
            _read_local_events_cache(mismatch) == [], "cache metadata mismatch ignored"
        )
    finally:
        path.unlink(missing_ok=True)
    return ok


def _fuzz_task_extraction() -> bool:
    """Verify task extraction covers direct Odoo model URLs and PR references."""
    print("\n[10] Task extraction")
    body = "https://www.qocinnovations.com/odoo/project.task/20551"
    ok = _assert(extract_tasks("", body) == ["20551"], "project.task URL")

    original_fetch = globals()["_fetch_pr_meta"]
    original_gh_str = globals()["_gh_str"]
    try:
        globals()["_fetch_pr_meta"] = lambda repo, num: {"ref": "25667#task"}
        globals()["_gh_str"] = lambda *args: ""
        ok &= _assert(
            _extract_referenced_pr_tasks("owner/repo", "See #119 for task")
            == ["25667"],
            "referenced PR fallback",
        )
    finally:
        globals()["_fetch_pr_meta"] = original_fetch
        globals()["_gh_str"] = original_gh_str
    return ok


def _fuzz_audit_warning_rows(config: Config) -> bool:
    """Verify high-risk output conditions create audit warnings."""
    print("\n[11] Output audit warnings")
    entry = TimeEntry(
        task_id="abc",
        repo="QOC-Innovations/fuzz-repo",
        pr_num=1,
        start=config.range_start,
        end=config.range_start + timedelta(hours=config.b_high, minutes=1),
    )
    sweep_result = SweepResults(
        gap_vals=[20],
        combined=[config.b_high * 3600 * config.num_days + 1],
        per_task={"UNKNOWN": [600]},
        scores=[-1.0],
        obs_mean=0.0,
        best_gap=20,
        best_total=config.b_high * 3600 * config.num_days + 1,
        best_score=-1.0,
        num_days=config.num_days,
    )
    result = TransformResult([entry], [entry], sweep_result)
    warnings = {warning for warning, _detail in _audit_warning_rows(result, config)}
    expected = {"over upper bound", "empty CSV Task/ID rows"}
    return _assert(expected <= warnings, f"warnings include {sorted(expected)}")


def _fuzz_unknown_events_are_audit_only(config: Config) -> bool:
    """Verify UNKNOWN source events do not affect billing output."""
    print("\n[12] UNKNOWN source events")
    repo = "QOC-Innovations/fuzz-repo"
    timestamp = config.range_start.astimezone(timezone.utc)
    events = [
        RawEvent(timestamp, ["UNKNOWN"], repo, 1, EventType.COMMIT, "no-task"),
        RawEvent(timestamp + timedelta(minutes=1), [], repo, 1, EventType.REVIEW, ""),
        RawEvent(
            timestamp + timedelta(minutes=5), ["12345"], repo, 2, EventType.COMMIT
        ),
    ]
    result = transform(events, config)
    buffer = io.StringIO()
    _write_unknown_sources(result, buffer)
    text = buffer.getvalue()
    ok = _assert(
        all(entry.task_id != "UNKNOWN" for entry in result.best_gap_entries),
        "UNKNOWN omitted from final entries",
    )
    ok &= _assert("UNKNOWN" not in result.sweep.per_task, "UNKNOWN omitted from sweep")
    ok &= _assert(
        "excluded from billing output" in text, "UNKNOWN listed in final table"
    )
    ok &= _assert(text.count("excluded from billing output") == 2, "empty tasks listed")
    return ok


def _fuzz_strategy_event_windows(config: Config) -> bool:
    """Verify strategies keep session and point-event windows separate."""
    print("\n[13] Strategy event windows")
    repo = "QOC-Innovations/fuzz-repo"
    timestamp = config.range_start.astimezone(timezone.utc)
    events = [
        RawEvent(timestamp, ["12345"], repo, 17, EventType.COMMIT),
        RawEvent(
            timestamp + timedelta(minutes=30), ["12345"], repo, 17, EventType.MERGE
        ),
        RawEvent(
            timestamp + timedelta(minutes=45), ["12345"], repo, 17, EventType.COMMIT
        ),
    ]
    windows = _build_window_entries(events, 60 * 60, config)
    dev_windows = [entry for entry in windows if entry.strategy_name == "development"]
    merge_windows = [entry for entry in windows if entry.strategy_name == "merge"]
    ok = _assert(len(dev_windows) == 1, "commit events remain sessionized")
    ok &= _assert(len(merge_windows) == 1, "merge event is a point entry")
    ok &= _assert(
        int((dev_windows[0].end - dev_windows[0].start).total_seconds()) == 45 * 60,
        "development session spans commit activity only",
    )
    ok &= _assert(
        int((merge_windows[0].end - merge_windows[0].start).total_seconds()) == 15 * 60,
        "merge point entry uses 15 minutes",
    )
    review_windows = _build_window_entries(
        [RawEvent(timestamp, ["54321"], repo, 18, EventType.REVIEW)],
        60 * 60,
        config,
    )
    ok &= _assert(len(review_windows) == 1, "review-only event creates a window")
    ok &= _assert(
        int((review_windows[0].end - review_windows[0].start).total_seconds())
        == 15 * 60,
        "review-only event uses fixed 15 minutes",
    )
    ok &= _assert(review_windows[0].strategy_category == "Review", "review metadata")
    return ok


def _bad_strategy_config(kind: str) -> SessionStrategyConfig:
    """Return a COMMIT config row with a caller-selected strategy kind."""
    return SessionStrategyConfig(
        "bad", "Bad", (EventType.COMMIT,), kind, ("strategy", "repo", "task_id")
    )


class _SpyStrategy(SessionizationStrategy):
    """Test strategy proving Context delegates through the Strategy interface."""

    called = False

    def build_entries(
        self, group: StrategyEventGroup, config: Config, gap_secs: int | None = None
    ) -> list[TimeEntry]:
        _SpyStrategy.called = True
        return WindowedSessionStrategy(self.settings).build_entries(
            group, config, gap_secs
        )


def _spy_strategy_context() -> SessionizationContext:
    """Return a context with COMMIT handled by a spy Strategy."""
    strategies: list[SessionizationStrategy] = []
    for settings in DEFAULT_SESSION_STRATEGY_CONFIGS:
        strategy = (
            _SpyStrategy(settings)
            if EventType.COMMIT in settings.event_types
            else _strategy_from_settings(settings)
        )
        strategies.append(strategy)
    return SessionizationContext(strategies)


def _fuzz_strategy_registry(config: Config) -> bool:
    """Verify strategy coverage, validation, and Context delegation."""
    print("\n[14] Strategy registry")
    context = _make_sessionization_context()
    ok = _assert(
        set(context.by_event_type) == set(EventType), "all event types covered"
    )
    try:
        _make_sessionization_context((_bad_strategy_config("missing"),))
        ok &= _assert(False, "invalid strategy kind rejected")
    except ValueError:
        ok &= _assert(True, "invalid strategy kind rejected")
    try:
        _make_sessionization_context((DEFAULT_SESSION_STRATEGY_CONFIGS[0],))
        ok &= _assert(False, "missing event strategy rejected")
    except ValueError:
        ok &= _assert(True, "missing event strategy rejected")
    event = RawEvent(config.range_start, ["12345"], "owner/repo", 1, EventType.COMMIT)
    _SpyStrategy.called = False
    entries = _spy_strategy_context().build_entries([event], 3600, config)
    ok &= _assert(len(entries) == 1, "spy context builds entries")
    ok &= _assert(_SpyStrategy.called, "context delegates to Strategy interface")
    return ok


def _fuzz_csv_entry(config: Config) -> TimeEntry:
    """Build a representative TimeEntry for CSV description tests."""
    repo = "QOC-Innovations/fuzz-repo"
    return TimeEntry(
        "12345",
        repo,
        1,
        config.range_start,
        config.range_start + timedelta(minutes=15),
        source_events=[
            RawEvent(config.range_start, ["12345"], repo, 1, EventType.COMMIT)
        ],
    )


def _prompt_excludes_technical_details(prompt: str, entry: TimeEntry) -> bool:
    """Verify the Claude prompt omits internal technical details."""
    ok = _assert(AI_DESCRIPTION_PREFIX not in prompt, "prompt excludes prefix")
    ok &= _assert("PR #98765" not in prompt, "prompt excludes PR numbers")
    ok &= _assert(entry.branch not in prompt, "prompt excludes branch names")
    ok &= _assert(entry.repo not in prompt, "prompt excludes repo names")
    ok &= _assert("Task ID" not in prompt, "prompt excludes task labels")
    ok &= _assert("Window:" not in prompt, "prompt excludes timestamps")
    return ok


def _technical_csv_entry(config: Config) -> TimeEntry:
    """Build a CSV entry containing technical details for scrub tests."""
    entry = _fuzz_csv_entry(config)
    entry.branch = "27654#no-ticket-events"
    entry.source_events[0].pr_num = 98765
    entry.source_events[0].subject = "PR #98765 commit 1234567 for billing dashboard"
    entry.source_events[0].pr_title = "Improve executive billing summary"
    entry.source_events[0].pr_body = (
        "Branch QOC-Innovations/qocinnovations adds task 12345"
    )
    return entry


def _fuzz_csv_description_output(config: Config) -> bool:
    """Verify CSV description prefix, fallback, sanitization, and project value."""
    print("\n[15] CSV descriptions")
    prompts: list[str] = []
    original = globals()["_call_claude_description"]
    original_available = globals()["_claude_available"]
    entry = _technical_csv_entry(config)
    try:
        globals()["_claude_available"] = lambda: True
        globals()["_call_claude_description"] = (
            lambda prompt: prompts.append(prompt)
            or 'PR #98765 commit 1234567 improved "bad", csv\ntext'
        )
        row = _entry_to_csv_row(entry, config)
        ok = _assert(
            row["Description"] == "[/] Development: improved bad csv text",
            "AI description is prefixed and sanitized",
        )
        ok &= _assert(row["Project/ID"] == entry.repo, "Project/ID is repo string")
        ok &= _prompt_excludes_technical_details(prompts[0], entry)
        globals()["_call_claude_description"] = lambda prompt: ""
        fallback = _entry_to_csv_row(entry, config)
        ok &= _assert(
            fallback["Description"]
            == "[/] Development: advanced project implementation",
            "Claude failure fallback",
        )
        globals()["_claude_available"] = lambda: False
        no_claude = _entry_to_csv_row(entry, config)
        ok &= _assert(
            no_claude["Description"]
            == "[/] Development: advanced project implementation",
            "missing Claude skips model call",
        )
    finally:
        globals()["_call_claude_description"] = original
        globals()["_claude_available"] = original_available
    return ok


def _fuzz_csv_description_pool(config: Config) -> bool:
    """Verify pooled CSV row generation preserves order and falls back on errors."""
    print("\n[16] CSV description pool")
    original = globals()["_csv_row_worker"]
    entries = [_fuzz_csv_entry(config) for _ in range(3)]
    for index, entry in enumerate(entries):
        entry.task_id = str(100 + index)

    def fake_worker(args: tuple[int, TimeEntry, Config, int]) -> tuple[int, dict]:
        index, entry, cfg, _total = args
        if index == 1:
            raise RuntimeError("boom")
        row = _fallback_csv_row(entry, cfg)
        row["Description"] = f"row-{index}"
        return index, row

    try:
        globals()["_csv_row_worker"] = fake_worker
        rows = _csv_rows_with_descriptions(entries, config)
        descriptions = [row["Description"] for row in rows]
        ok = _assert(
            descriptions
            == ["row-0", "[/] Development: advanced project implementation", "row-2"],
            "pool preserves row order",
        )
        ok &= _assert(
            rows[1]["Project/ID"] == entries[1].repo, "worker error fallback row"
        )
        ok &= _assert(CLAUDE_DESCRIPTION_CONCURRENCY == 8, "pool concurrency is 8")
    finally:
        globals()["_csv_row_worker"] = original
    return ok


def _fuzz_tuner_helpers(config: Config) -> bool:
    """Verify tuner config round-trip, mutation, evaluation, and persistence."""
    print("\n[17] Tuner helpers")
    original_path = globals()["TUNING_CONFIG_FILE"]
    globals()["TUNING_CONFIG_FILE"] = f"/tmp/timelog_tuning_fuzz_{os.getpid()}.json"
    path = _tuning_config_path()
    try:
        payload = _default_tuning_payload()
        cfg = _config_from_tuning_payload(payload)
        ok = _assert(cfg.min_task_minutes == 15, "tuning payload round-trips")
        population = _candidate_population(payload, 1)
        ok &= _assert(len(population) == 5, "candidate population generated")
        candidate = population[1]
        _evaluate_candidate(candidate, _synthetic_tuner_events(config))
        ok &= _assert(candidate.summary["rows"] > 0, "candidate evaluates")
        _write_tuning_payload(candidate.payload)
        loaded = _load_tuning_payload()
        ok &= _assert(loaded == candidate.payload, "winner persists to disk")
        bad = json.loads(json.dumps(payload))
        bad["config"]["b_low"] = bad["config"]["b_high"]
        try:
            _config_from_tuning_payload(bad)
            ok &= _assert(False, "invalid tuning config rejected")
        except ValueError:
            ok &= _assert(True, "invalid tuning config rejected")
    finally:
        path.unlink(missing_ok=True)
        globals()["TUNING_CONFIG_FILE"] = original_path
    return ok


def _fuzz_mermaid_gantt_output(config: Config) -> bool:
    """Verify Mermaid Gantt output is generated with sanitized labels."""
    print("\n[18] Mermaid Gantt output")
    entry = TimeEntry(
        task_id="UNKNOWN",
        repo="QOC:bad,repo",
        pr_num=7,
        start=config.range_start,
        end=config.range_start + timedelta(minutes=15),
        label="QOC:bad,repo#7",
    )
    buffer = io.StringIO()
    _write_mermaid_gantt(
        "Window Diagram",
        [entry],
        buffer,
        chart_start=config.range_start,
        chart_end=config.range_end - timedelta(minutes=1),
    )
    text = buffer.getvalue()
    line = next(line for line in text.splitlines() if "entry_0001" in line)
    ok = _assert("```mermaid" in text, "Mermaid fence is present")
    ok &= _assert("gantt" in text, "Gantt diagram is selected")
    ok &= _assert("tickInterval 1day" in text, "grid ticks are daily")
    ok &= _assert("range_start" in text, "left boundary anchor is present")
    ok &= _assert("range_end" in text, "right boundary anchor is present")
    ok &= _assert("Work start" in text, "workday start markers are present")
    ok &= _assert("Work end" in text, "workday end markers are present")
    ok &= _assert(" : vert, " in text, "workday markers are vertical lines")
    ok &= _assert(
        _mermaid_datetime(config.range_end) not in text,
        "exclusive next-day boundary is not charted",
    )
    ok &= _assert("section Task UNKNOWN" in text, "entries are grouped by task")
    ok &= _assert(line.strip().startswith(". :entry_0001"), "labels are minimal")
    return ok


def _run_fuzzer() -> None:
    """Run the full fuzzer suite and exit with 0 (all pass) or 1 (any fail)."""
    print("=" * 60)
    print("timelog fuzzer")
    print("=" * 60)
    config = _make_config()
    all_passed = (
        _fuzz_score_invariants(config)
        & _fuzz_crash_free_sweep(config)
        & _fuzz_sweep_monotonicity(config)
        & _fuzz_multi_task_window_split(config)
        & _fuzz_daily_score_best_gap(config)
        & _fuzz_invalid_sweep_minimum()
        & _fuzz_target_date_exclusions()
        & _fuzz_repo_name_normalization()
        & _fuzz_local_event_cache(config)
        & _fuzz_task_extraction()
        & _fuzz_audit_warning_rows(config)
        & _fuzz_unknown_events_are_audit_only(config)
        & _fuzz_strategy_event_windows(config)
        & _fuzz_strategy_registry(config)
        & _fuzz_csv_description_output(config)
        & _fuzz_csv_description_pool(config)
        & _fuzz_tuner_helpers(config)
        & _fuzz_mermaid_gantt_output(config)
    )
    print()
    print("=" * 60)
    print("OVERALL:", "PASS" if all_passed else "FAIL")
    print("=" * 60)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fuzz":
        _run_fuzzer()
    elif len(sys.argv) > 1 and sys.argv[1] == "--tune":
        _run_tuner()
    else:
        main()

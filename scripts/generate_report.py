#!/usr/bin/env python3
"""Generate a unified quality-report.md from all JSON tool outputs under reports/."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
REPORTS = REPO_ROOT / "reports"
OUT_FILE = REPORTS / "quality-report.md"

# ── Thresholds ────────────────────────────────────────────────────────────────
COVERAGE_WARN = 95.0
COVERAGE_FAIL = 90.0
KILL_RATE_WARN = 95.0
KILL_RATE_FAIL = 90.0
CC_RANKS = {"A": "✅", "B": "✅", "C": "⚠️", "D": "⚠️", "E": "❌", "F": "❌"}
MI_RANKS = {"A": "✅", "B": "⚠️", "C": "❌"}
COMPLEXITY_THRESHOLD = 15


def badge(value: float, warn: float, fail: float, fmt: str = ".1f") -> str:
    icon = "✅" if value >= warn else ("⚠️" if value >= fail else "❌")
    return f"{icon} **{value:{fmt}}%**"


def cc_badge(rank: str) -> str:
    return CC_RANKS.get(rank, "❓")


def mi_badge(rank: str) -> str:
    return MI_RANKS.get(rank, "❓")


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def short_path(full: str) -> str:
    """Strip repo root prefix for display."""
    try:
        return str(Path(full).relative_to(REPO_ROOT))
    except ValueError:
        return full


def _cc_rank(score: int) -> str:
    if score <= 5:
        return "A"
    if score <= 10:
        return "B"
    if score <= 15:
        return "C"
    if score <= 20:
        return "D"  # noqa: PLR2004
    if score <= 40:
        return "E"  # noqa: PLR2004
    return "F"


# ── Section builders ──────────────────────────────────────────────────────────

def section_coverage(lines: list[str]) -> None:
    data = load_json(REPORTS / "coverage" / "coverage.json")
    lines.append("## 🧪 Coverage\n")
    if data is None:
        lines.append("> _No coverage.json found — run `uv run coverage json`_\n")
        return

    totals = data.get("totals", {})
    pct = totals.get("percent_covered", 0.0)
    stmts = totals.get("num_statements", 0)
    miss = totals.get("missing_lines", 0)
    covered_branches = totals.get("covered_branches", 0)
    total_branches = totals.get("num_branches", 0)
    branch_pct = (covered_branches / total_branches * 100) if total_branches else 0.0

    lines.append(f"| Metric | Value |\n|---|---|\n"
                 f"| **Overall** | {badge(pct, COVERAGE_WARN, COVERAGE_FAIL)} |\n"
                 f"| Statements | {stmts - miss} / {stmts} |\n"
                 f"| Missing | {miss} |\n"
                 f"| Branches | {covered_branches} / {total_branches} ({branch_pct:.1f}%) |\n")
    lines.append("")

    files = data.get("files", {})
    if files:
        lines.append("### Per-file\n")
        lines.append("| File | Stmts | Miss | Branch | Cover |")
        lines.append("|---|--:|--:|--:|---:|")
        rows = []
        for path_str, fdata in files.items():
            s = fdata.get("summary", {})
            f_pct = s.get("percent_covered", 0.0)
            icon = "✅" if f_pct >= COVERAGE_WARN else ("⚠️" if f_pct >= COVERAGE_FAIL else "❌")
            rows.append((
                f_pct,
                short_path(path_str),
                s.get("num_statements", 0),
                s.get("missing_lines", 0),
                f"{s.get('covered_branches', 0)}/{s.get('num_branches', 0)}",
                f"{icon} {f_pct:.1f}%",
            ))
        rows.sort(key=lambda r: r[0])
        for row in rows:
            lines.append(f"| `{row[1]}` | {row[2]} | {row[3]} | {row[4]} | {row[5]} |")
        lines.append("")


def section_mutation(lines: list[str]) -> None:
    data = load_json(REPORTS / "mutation" / "mutation.json")
    lines.append("## 🧬 Mutation Testing\n")
    if data is None:
        lines.append("> _No mutation.json found — run `uv run ./scripts/mutation-test.sh`_\n")
        return

    if not isinstance(data, list):
        lines.append("> _Unexpected mutation.json format_\n")
        return

    total = len(data)
    completed = [m for m in data if m.get("test_outcome") is not None]
    killed = [m for m in completed if m.get("test_outcome") == "killed"]
    survived = [m for m in completed if m.get("test_outcome") != "killed"]
    pending = total - len(completed)

    kill_rate = len(killed) / len(completed) * 100 if completed else 0.0

    lines.append(f"| Metric | Value |\n|---|---|\n"
                 f"| **Kill Rate** | {badge(kill_rate, KILL_RATE_WARN, KILL_RATE_FAIL)} |\n"
                 f"| Total Mutants | {total} |\n"
                 f"| Killed | {len(killed)} |\n"
                 f"| Survived | {len(survived)} |\n"
                 f"| Pending | {pending} |\n")
    lines.append("")

    if survived:
        lines.append("### 🔴 Surviving Mutants\n")
        lines.append("| Module | Operator | Occurrence | Position |")
        lines.append("|---|---|--:|---|")
        for m in survived:
            start = m.get("start_pos", "")
            end = m.get("end_pos", "")
            pos = f"{start}–{end}" if start and end else str(start or "")
            lines.append(
                f"| `{m.get('module', '?')}` "
                f"| `{m.get('operator', '?')}` "
                f"| {m.get('occurrence', '?')} "
                f"| {pos} |"
            )
        lines.append("")


def section_cyclomatic(lines: list[str]) -> None:
    data = load_json(REPORTS / "radon" / "cc.json")
    lines.append("## 🔁 Cyclomatic Complexity\n")
    if data is None:
        lines.append("> _No cc.json found — run `uv run bash scripts/static-analysis.sh`_\n")
        return

    # Flatten all blocks across all files
    all_blocks: list[dict] = []
    rank_counts: dict[str, int] = {r: 0 for r in "ABCDEF"}
    for file_path, blocks in data.items():
        for block in blocks:
            rank = block.get("rank", _cc_rank(block.get("complexity", 0)))
            rank_counts[rank] = rank_counts.get(rank, 0) + 1
            all_blocks.append({
                "file": short_path(file_path),
                "name": block.get("name", "?"),
                "type": block.get("type", "?"),
                "complexity": block.get("complexity", 0),
                "rank": rank,
                "lineno": block.get("lineno", 0),
            })

    lines.append("### Rank Distribution\n")
    lines.append("| Rank | Score Range | Label | Count |")
    lines.append("|---|---|---|--:|")
    rank_labels = [
        ("A", "1–5", "Simple"),
        ("B", "6–10", "Well-structured"),
        ("C", "11–15", "Moderate"),
        ("D", "16–20", "Complex"),
        ("E", "21–40", "Alarming"),
        ("F", "41+", "Error-prone"),
    ]
    for rank, rng, label in rank_labels:
        count = rank_counts.get(rank, 0)
        icon = CC_RANKS.get(rank, "")
        lines.append(f"| {icon} **{rank}** | {rng} | {label} | {count} |")
    lines.append("")

    if all_blocks:
        top = sorted(all_blocks, key=lambda b: b["complexity"], reverse=True)[:15]
        lines.append("### Top 15 Most Complex Blocks\n")
        lines.append("| File | Block | Type | Complexity | Rank |")
        lines.append("|---|---|---|--:|---|")
        for b in top:
            icon = CC_RANKS.get(b["rank"], "")
            lines.append(
                f"| `{b['file']}` | `{b['name']}` | {b['type']} "
                f"| {b['complexity']} | {icon} {b['rank']} |"
            )
        lines.append("")


def section_maintainability(lines: list[str]) -> None:
    data = load_json(REPORTS / "radon" / "mi.json")
    lines.append("## 🛠 Maintainability Index\n")
    if data is None:
        lines.append("> _No mi.json found — run `uv run bash scripts/static-analysis.sh`_\n")
        return

    lines.append("_Scale: A (100–20) = high · B (19–10) = medium · C (9–0) = low_\n")
    lines.append("| File | MI | Rank |")
    lines.append("|---|--:|---|")
    rows = []
    for file_path, info in data.items():
        mi = info.get("mi", 0.0)
        rank = info.get("rank", "C")
        rows.append((mi, short_path(file_path), rank))
    rows.sort(key=lambda r: r[0], reverse=True)
    for mi, fp, rank in rows:
        icon = MI_RANKS.get(rank, "")
        lines.append(f"| `{fp}` | {mi:.1f} | {icon} {rank} |")
    lines.append("")


def section_raw(lines: list[str]) -> None:
    data = load_json(REPORTS / "radon" / "raw.json")
    lines.append("## 📐 Raw Metrics\n")
    if data is None:
        lines.append("> _No raw.json found — run `uv run bash scripts/static-analysis.sh`_\n")
        return

    # Aggregate totals
    totals = {"loc": 0, "lloc": 0, "sloc": 0, "comments": 0, "multi": 0, "blank": 0}
    file_rows = []
    for file_path, info in data.items():
        if isinstance(info, dict) and "loc" in info:
            for k in totals:
                totals[k] += info.get(k, 0)
            file_rows.append((short_path(file_path), info))

    lines.append("### Totals\n")
    lines.append("| LOC | LLOC | SLOC | Comments | Multi | Blank |")
    lines.append("|--:|--:|--:|--:|--:|--:|")
    lines.append(
        f"| {totals['loc']} | {totals['lloc']} | {totals['sloc']} "
        f"| {totals['comments']} | {totals['multi']} | {totals['blank']} |"
    )
    lines.append("")

    if file_rows:
        file_rows.sort(key=lambda r: r[1].get("sloc", 0), reverse=True)
        lines.append("### Per-file (sorted by SLOC)\n")
        lines.append("| File | LOC | LLOC | SLOC | Comments | Blank |")
        lines.append("|---|--:|--:|--:|--:|--:|")
        for fp, info in file_rows:
            lines.append(
                f"| `{fp}` | {info.get('loc',0)} | {info.get('lloc',0)} "
                f"| {info.get('sloc',0)} | {info.get('comments',0)} | {info.get('blank',0)} |"
            )
        lines.append("")


def section_halstead(lines: list[str]) -> None:
    data = load_json(REPORTS / "radon" / "hal.json")
    lines.append("## 🔬 Halstead Metrics\n")
    if data is None:
        lines.append("> _No hal.json found — run `uv run bash scripts/static-analysis.sh`_\n")
        return

    rows = []
    for file_path, metrics in data.items():
        # radon hal --json: file → {total: {…}} or file → list
        m = metrics.get("total", metrics) if isinstance(metrics, dict) else {}
        if not m or "volume" not in m:
            continue
        rows.append((
            short_path(file_path),
            round(m.get("volume", 0), 1),
            round(m.get("effort", 0), 1),
            round(m.get("bugs", 0), 3),
            round(m.get("time", 0), 1),
            m.get("vocabulary", 0),
            m.get("length", 0),
        ))

    if not rows:
        lines.append("> _No Halstead data available_\n")
        return

    rows.sort(key=lambda r: r[1], reverse=True)
    lines.append("_Volume = program size · Effort = mental effort · Bugs = estimated defects_\n")
    lines.append("| File | Volume | Effort | Bugs | Time (s) | Vocab | Length |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for fp, vol, effort, bugs, time_, vocab, length in rows:
        lines.append(
            f"| `{fp}` | {vol} | {effort} | {bugs} | {time_} | {vocab} | {length} |"
        )
    lines.append("")


def section_complexipy(lines: list[str]) -> None:
    data = load_json(REPORTS / "complexipy" / "complexipy-results.json")
    lines.append("## 🧠 Cognitive Complexity (complexipy)\n")
    if data is None:
        lines.append("> _No complexipy-results.json found — run `uv run bash scripts/static-analysis.sh`_\n")
        return

    if not isinstance(data, list):
        lines.append("> _Unexpected complexipy-results.json format_\n")
        return

    threshold = COMPLEXITY_THRESHOLD
    violations = [f for f in data if f.get("complexity", 0) > threshold]
    ok = len(data) - len(violations)

    lines.append(f"_Threshold: **{threshold}** · functions above threshold are flagged ❌_\n")
    lines.append(f"| Metric | Value |\n|---|---|\n"
                 f"| Total functions | {len(data)} |\n"
                 f"| ✅ Within threshold | {ok} |\n"
                 f"| ❌ Violations | {len(violations)} |\n")
    lines.append("")

    if violations:
        lines.append("### ❌ Violations\n")
        lines.append("| File | Function | Complexity |")
        lines.append("|---|---|--:|")
        for f in sorted(violations, key=lambda x: x.get("complexity", 0), reverse=True):
            full = f.get("path") or f.get("file_name", "?")
            lines.append(
                f"| `{full}` | `{f.get('function_name', '?')}` | ❌ **{f.get('complexity', '?')}** |"
            )
        lines.append("")

    if data:
        lines.append("### All Functions (sorted by complexity)\n")
        lines.append("| File | Function | Complexity |")
        lines.append("|---|---|--:|")
        for f in sorted(data, key=lambda x: x.get("complexity", 0), reverse=True):
            full = f.get("path") or f.get("file_name", "?")
            score = f.get("complexity", 0)
            icon = "❌" if score > threshold else ("⚠️" if score >= threshold * 0.8 else "✅")
            lines.append(f"| `{full}` | `{f.get('function_name', '?')}` | {icon} {score} |")
        lines.append("")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    lines: list[str] = []
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"# Quality Report\n")
    lines.append(f"_Generated: {now}_\n")
    lines.append("---\n")

    lines.append("## Contents\n")
    lines.append("| # | Section |\n|---|---|")
    sections = [
        "Coverage",
        "Mutation Testing",
        "Cyclomatic Complexity",
        "Maintainability Index",
        "Raw Metrics",
        "Halstead Metrics",
        "Cognitive Complexity (complexipy)",
    ]
    for i, s in enumerate(sections, 1):
        lines.append(f"| {i} | {s} |")
    lines.append("\n---\n")

    section_coverage(lines)
    lines.append("---\n")
    section_mutation(lines)
    lines.append("---\n")
    section_cyclomatic(lines)
    lines.append("---\n")
    section_maintainability(lines)
    lines.append("---\n")
    section_raw(lines)
    lines.append("---\n")
    section_halstead(lines)
    lines.append("---\n")
    section_complexipy(lines)

    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(lines) + "\n")
    print(f"Report written to {OUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

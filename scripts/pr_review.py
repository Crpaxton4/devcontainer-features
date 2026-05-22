#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"


class GitCommandError(RuntimeError):
    def __init__(self, args: Sequence[str], message: str) -> None:
        command = " ".join(args)
        super().__init__(f"git command failed: git {command}\n{message}")


@dataclass(frozen=True)
class ReviewSummary:
    file_count: int
    additions: int
    deletions: int
    binary_files: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a PR-style terminal review for a local branch.",
        epilog=(
            "Examples:\n"
            "  scripts/pr-review.sh\n"
            "  scripts/pr-review.sh --summary-only\n"
            "  scripts/pr-review.sh --path src/odoo_service/odoo_recordset.py\n"
            "  scripts/pr-review.sh --base release/1.0 --max-lines-per-file 0"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base",
        default="main",
        help="Base branch or commit to review against (default: main).",
    )
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Head branch or commit to review (default: HEAD).",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Limit the review to a path. Repeat to include multiple paths.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=5,
        help="Number of diff context lines to show per hunk (default: 5).",
    )
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=160,
        help="Maximum patch lines to print for each file. Use 0 to disable truncation.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print branch, commit, and file summaries without per-file patches.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in the output.",
    )
    args = parser.parse_args()
    if args.context < 0:
        parser.error("--context must be >= 0")
    if args.max_lines_per_file < 0:
        parser.error("--max-lines-per-file must be >= 0")
    return args


def run_git(
    args: Sequence[str], *, color: bool = False, allow_failure: bool = False
) -> str:
    color_mode = "always" if color else "never"
    completed = subprocess.run(
        ["git", "-c", f"color.ui={color_mode}", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout if completed.returncode == 0 else completed.stderr
    if completed.returncode != 0 and not allow_failure:
        raise GitCommandError(args, output.strip() or "unknown git error")
    return output


def style(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def print_section(title: str, *, use_color: bool) -> None:
    print()
    print(style(title, BOLD + CYAN, use_color))
    print(style("-" * len(title), DIM, use_color))


def print_key_value(label: str, value: str) -> None:
    print(f"{label:<12} {value}")


def current_branch() -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def ensure_ref_exists(ref: str) -> None:
    run_git(["rev-parse", "--verify", ref])


def short_sha(ref: str) -> str:
    return run_git(["rev-parse", "--short", ref]).strip()


def merge_base(base: str, head: str) -> str:
    return run_git(["merge-base", base, head]).strip()


def working_tree_status() -> str:
    return run_git(["status", "--short"])


def commit_lines(commit_range: str, *, use_color: bool) -> list[str]:
    output = run_git(
        ["log", "--reverse", "--oneline", "--decorate", commit_range],
        color=use_color,
    )
    return [line for line in output.splitlines() if line.strip()]


def changed_paths(diff_range: str, paths: Sequence[str]) -> list[str]:
    args = ["diff", "--name-only", "--find-renames", "-z", diff_range]
    if paths:
        args.extend(["--", *paths])
    output = run_git(args)
    return [path for path in output.split("\0") if path]


def summarize_changes(diff_range: str, paths: Sequence[str]) -> ReviewSummary:
    args = ["diff", "--numstat", "--find-renames", diff_range]
    if paths:
        args.extend(["--", *paths])

    additions = 0
    deletions = 0
    binary_files = 0
    file_count = 0

    for line in run_git(args).splitlines():
        if not line.strip():
            continue
        file_count += 1
        added, removed, _ = line.split("\t", 2)
        if added == "-" or removed == "-":
            binary_files += 1
            continue
        additions += int(added)
        deletions += int(removed)

    return ReviewSummary(
        file_count=file_count,
        additions=additions,
        deletions=deletions,
        binary_files=binary_files,
    )


def diffstat(diff_range: str, paths: Sequence[str], *, use_color: bool) -> str:
    args = ["diff", "--find-renames", "--stat", "--summary", diff_range]
    if paths:
        args.extend(["--", *paths])
    return run_git(args, color=use_color).rstrip()


def patch_for_path(
    diff_range: str,
    path: str,
    *,
    context: int,
    use_color: bool,
) -> str:
    return run_git(
        [
            "diff",
            "--find-renames",
            f"--unified={context}",
            "--patch",
            "--summary",
            diff_range,
            "--",
            path,
        ],
        color=use_color,
    ).rstrip()


def truncate_patch(
    patch: str,
    *,
    max_lines: int,
    path: str,
    use_color: bool,
) -> str:
    if max_lines == 0:
        return patch

    lines = patch.splitlines()
    if len(lines) <= max_lines:
        return patch

    remaining = len(lines) - max_lines
    message = (
        f"... truncated {remaining} additional line(s) for {path}. "
        f"Rerun with --max-lines-per-file 0 or --path {path}."
    )
    return "\n".join(lines[:max_lines] + [style(message, YELLOW, use_color)])


def render_header(
    *,
    branch_name: str,
    base: str,
    head: str,
    merge_base_sha: str,
    summary: ReviewSummary,
    commit_count: int,
    filtered_paths: Sequence[str],
    working_tree: str,
    use_color: bool,
) -> None:
    width = min(shutil.get_terminal_size((100, 20)).columns, 120)
    print(style("=" * width, DIM, use_color))
    print(style("PR-Style Branch Review", BOLD + CYAN, use_color))
    print(style("=" * width, DIM, use_color))

    print_key_value("Branch", f"{branch_name} -> {base}")
    print_key_value("Head", head)
    print_key_value("Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print_key_value("Merge base", short_sha(merge_base_sha))
    print_key_value("Commits", str(commit_count))
    print_key_value(
        "Files",
        f"{summary.file_count} (+{summary.additions} / -{summary.deletions})",
    )
    if summary.binary_files:
        print_key_value("Binary", str(summary.binary_files))
    if filtered_paths:
        print_key_value("Filtered", ", ".join(filtered_paths))

    if working_tree.strip():
        warning = "Working tree has uncommitted changes; review output only covers committed diff."
        print_key_value("Warning", style(warning, YELLOW, use_color))


def main() -> int:
    args = parse_args()
    use_color = sys.stdout.isatty() and not args.no_color

    try:
        ensure_ref_exists(args.base)
        ensure_ref_exists(args.head)
        branch_name = current_branch()
        merge_base_sha = merge_base(args.base, args.head)
        diff_range = f"{args.base}...{args.head}"
        commit_range = f"{args.base}..{args.head}"
        summary = summarize_changes(diff_range, args.path)
        commits = commit_lines(commit_range, use_color=use_color)
        paths = changed_paths(diff_range, args.path)
        worktree = working_tree_status()
    except GitCommandError as error:
        print(str(error), file=sys.stderr)
        return 1

    render_header(
        branch_name=branch_name,
        base=args.base,
        head=args.head,
        merge_base_sha=merge_base_sha,
        summary=summary,
        commit_count=len(commits),
        filtered_paths=args.path,
        working_tree=worktree,
        use_color=use_color,
    )

    print_section("Commits", use_color=use_color)
    if commits:
        for line in commits:
            print(line)
    else:
        print(style("No branch commits to review.", DIM, use_color))

    print_section("Changed Files", use_color=use_color)
    stat_output = diffstat(diff_range, args.path, use_color=use_color)
    if stat_output:
        print(stat_output)
    else:
        print(style("No committed file changes to review.", DIM, use_color))

    if args.summary_only or not paths:
        return 0

    print_section("Patches", use_color=use_color)
    for index, path in enumerate(paths, start=1):
        label = f"[{index}/{len(paths)}] {path}"
        print(style(label, BOLD, use_color))
        patch = patch_for_path(
            diff_range,
            path,
            context=args.context,
            use_color=use_color,
        )
        if not patch.strip():
            print(style("No patch body for this path.", DIM, use_color))
            print()
            continue

        print(
            truncate_patch(
                patch,
                max_lines=args.max_lines_per_file,
                path=path,
                use_color=use_color,
            )
        )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
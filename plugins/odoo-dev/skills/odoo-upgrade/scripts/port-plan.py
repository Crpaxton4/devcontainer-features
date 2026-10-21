#!/usr/bin/env python3
"""
Compute the port plan for a module tree: the order, the hops and the grep hits.

Usage:
    port-plan.py --from S --to T --worktree W --artifacts A

Writes A/plan.json and prints a one-line summary on stdout:

    {"from", "to", "hops": [...], "order": [...],
     "items": [{"module", "depends": [...],
                "hits": [{"series", "pattern", "file", "line", "text"}]}]}

- order: module names, dependencies first, over the `depends` that point at
  modules inside the tree. Dependencies outside the tree are ignored.
- hops: the series after S up to and including T, over 16.0, 17.0, 18.0, 19.0.
- hits: every line of a module that matches a row of
  references/detection-patterns.tsv whose series is one of the hops.

This script parses `__manifest__.py` itself (ast.literal_eval). The other
manifest parsers in this plugin are not changed.

Exit: 0 ok, 2 usage error or a dependency cycle.
"""

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

SERIES = ["16.0", "17.0", "18.0", "19.0"]
PATTERNS = (
    Path(__file__).resolve().parent.parent / "references" / "detection-patterns.tsv"
)
SKIP_DIRS = {".git", "__pycache__", "node_modules"}
TEXT_LIMIT = 200


def fail(message: str) -> None:
    print(f"port-plan.py: {message}", file=sys.stderr)
    sys.exit(2)


def find_modules(worktree: Path) -> dict[str, tuple[Path, list[str]]]:
    """Module name -> (directory, depends) for each __manifest__.py in the tree."""
    modules = {}
    for root, dirs, files in os.walk(worktree):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if "__manifest__.py" not in files:
            continue
        path = Path(root)
        try:
            manifest = ast.literal_eval((path / "__manifest__.py").read_text())
        except (ValueError, SyntaxError) as err:
            fail(f"{path / '__manifest__.py'}: not a literal manifest: {err}")
        depends = manifest.get("depends", []) if isinstance(manifest, dict) else []
        modules[path.name] = (path, [str(d) for d in depends])
    return modules


def topological_order(modules: dict[str, tuple[Path, list[str]]]) -> list[str]:
    order, state = [], {}

    def visit(name: str, trail: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "open":
            cycle = trail[trail.index(name) :] + [name]
            fail("dependency cycle: " + " -> ".join(cycle))
        state[name] = "open"
        for dep in sorted(modules[name][1]):
            if dep in modules:
                visit(dep, trail + [name])
        state[name] = "done"
        order.append(name)

    for name in sorted(modules):
        visit(name, [])
    return order


def load_patterns(hops: list[str]) -> list[dict]:
    rows = []
    for number, line in enumerate(PATTERNS.read_text().splitlines()):
        if number == 0 or not line.strip():
            continue
        series, pattern, globs, note = (line.split("\t") + ["", "", "", ""])[:4]
        if series in hops:
            rows.append(
                {
                    "series": series,
                    "pattern": pattern,
                    "regex": re.compile(pattern),
                    "globs": [g for g in globs.split(",") if g],
                    "note": note,
                }
            )
    return rows


def wanted(relative: str, globs: list[str]) -> bool:
    if not globs:
        return True
    name = os.path.basename(relative)
    return any(fnmatch.fnmatch(relative if "/" in g else name, g) for g in globs)


def find_hits(module_dir: Path, rows: list[dict]) -> list[dict]:
    hits = []
    for root, dirs, files in os.walk(module_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for filename in sorted(files):
            full = Path(root) / filename
            relative = full.relative_to(module_dir).as_posix()
            applicable = [r for r in rows if wanted(relative, r["globs"])]
            if not applicable:
                continue
            try:
                lines = full.read_text(errors="strict").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, text in enumerate(lines, 1):
                for row in applicable:
                    if row["regex"].search(text):
                        hits.append(
                            {
                                "series": row["series"],
                                "pattern": row["pattern"],
                                "file": relative,
                                "line": number,
                                "text": text.strip()[:TEXT_LIMIT],
                            }
                        )
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute the port plan.")
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--to", dest="target", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--artifacts", required=True)
    try:
        args = parser.parse_args()
    except SystemExit as err:
        sys.exit(2 if err.code else 0)

    for label, value in (("--from", args.source), ("--to", args.target)):
        if value not in SERIES:
            fail(f"{label} {value!r} is not one of {', '.join(SERIES)}")
    if SERIES.index(args.source) >= SERIES.index(args.target):
        fail(f"--from {args.source} must be earlier than --to {args.target}")
    worktree = Path(args.worktree)
    if not worktree.is_dir():
        fail(f"--worktree {args.worktree!r} is not a directory")

    hops = SERIES[SERIES.index(args.source) + 1 : SERIES.index(args.target) + 1]
    modules = find_modules(worktree)
    order = topological_order(modules)
    rows = load_patterns(hops)
    items = [
        {
            "module": name,
            "depends": modules[name][1],
            "hits": find_hits(modules[name][0], rows),
        }
        for name in order
    ]

    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    plan = {
        "from": args.source,
        "to": args.target,
        "hops": hops,
        "order": order,
        "items": items,
    }
    (artifacts / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    total = sum(len(i["hits"]) for i in items)
    print(
        f"plan.json: {len(order)} modules, hops {' '.join(hops)}, "
        f"{total} grep hits -> {artifacts / 'plan.json'}"
    )


if __name__ == "__main__":
    main()

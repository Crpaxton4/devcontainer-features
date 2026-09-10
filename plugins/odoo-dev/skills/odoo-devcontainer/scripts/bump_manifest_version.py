#!/usr/bin/env python3
"""
Bump __manifest__.py version based on conventional commits since main.

Usage:
    python bump_manifest_version.py [MODULE_PATH]

MODULE_PATH defaults to cwd. Must contain __manifest__.py.

Bump logic (derived from commits since main/master):
    BREAKING CHANGE footer or <type>!  → major (resets minor + patch to 0)
    feat                               → minor (resets patch to 0)
    anything else                      → patch
"""
import ast
import re
import subprocess
import sys
from pathlib import Path


def get_commits(cwd: Path) -> list[str]:
    for base in ("origin/main", "origin/master", "main", "master"):
        try:
            out = subprocess.check_output(
                ["git", "log", f"{base}..HEAD", "--format=%s%n%b", "--no-merges"],
                text=True, stderr=subprocess.DEVNULL, cwd=cwd,
            )
            lines = [l for l in out.splitlines() if l.strip()]
            if lines:
                return lines
        except subprocess.CalledProcessError:
            pass
    # Fallback: last commit only
    out = subprocess.check_output(
        ["git", "log", "-1", "--format=%s%n%b"], text=True, cwd=cwd
    )
    return [l for l in out.splitlines() if l.strip()]


def determine_bump(commits: list[str]) -> str:
    bump = "patch"
    for line in commits:
        if "BREAKING CHANGE" in line:
            return "major"
        if re.match(r"^\w+(\([^)]+\))?!:", line):
            return "major"
        if re.match(r"^feat(\([^)]+\))?:", line) and bump == "patch":
            bump = "minor"
    return bump


def parse_version(v: str) -> tuple[str, int, int, int]:
    parts = v.split(".")
    if len(parts) != 5:
        raise ValueError(f"Expected X.Y.A.B.C (e.g. 17.0.1.0.0), got: {v!r}")
    return f"{parts[0]}.{parts[1]}", int(parts[2]), int(parts[3]), int(parts[4])


def apply_bump(version: str, bump: str) -> str:
    series, major, minor, patch = parse_version(version)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{series}.{major}.{minor}.{patch}"


def get_current_version(manifest_path: Path) -> str:
    manifest = ast.literal_eval(manifest_path.read_text())
    v = manifest.get("version", "")
    if not v:
        raise ValueError("No 'version' key in manifest")
    return v


def set_version(manifest_path: Path, new_version: str) -> None:
    content = manifest_path.read_text()
    updated = re.sub(
        r"""(["']version["']\s*:\s*)(["'])([^"']+)\2""",
        lambda m: f"{m.group(1)}{m.group(2)}{new_version}{m.group(2)}",
        content,
        count=1,
    )
    if updated == content:
        raise ValueError("Could not find/replace 'version' in manifest")
    manifest_path.write_text(updated)


def main() -> None:
    module_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    manifest_path = module_path / "__manifest__.py"

    if not manifest_path.exists():
        sys.exit(f"ERROR: {manifest_path} not found")

    current = get_current_version(manifest_path)
    commits = get_commits(module_path)
    if not commits:
        sys.exit("ERROR: No commits found")

    bump = determine_bump(commits)
    new_version = apply_bump(current, bump)
    set_version(manifest_path, new_version)
    print(f"{current} → {new_version}  ({bump})")


if __name__ == "__main__":
    main()

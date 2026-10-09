#!/usr/bin/env python3
"""
Bump __manifest__.py version based on conventional commits since main.

Usage:
    python bump_manifest_version.py [MODULE_PATH]

MODULE_PATH defaults to cwd. Must contain __manifest__.py.

Accepted version forms — the four Odoo itself accepts. Odoo is not vendored
here, so this is implemented from the rule in `adapt_version`
(odoo/modules/module.py): a version that does not start with `<series>.` gets
the series prepended, and what is left after the series must be two or three
numeric parts (`^[0-9]+\\.[0-9]+(?:\\.[0-9]+)?$`).

    A.B              1.0         series comes from ODOO_VERSION
    A.B.C            1.0.0       series comes from ODOO_VERSION
    <series>.A.B     18.0.0.4    series read off the version itself
    <series>.A.B.C   18.0.1.2.3  canonical — nothing to infer

The canonical form is `<series>.A.B.C`. A shorter form is normalized to it
*first* and written back, then the bump is applied on top of the normalized
version: `18.0.0.4` becomes `18.0.0.4.0`, and then `18.0.0.4.1` on a patch
bump. Both steps are printed, so repos converge on the canonical form.

A series-less version (`A.B` / `A.B.C`) has to get its series from somewhere:
it is read from ODOO_VERSION, the devcontainer's Odoo series (e.g. `19.0`).
With ODOO_VERSION unset the script exits non-zero instead of guessing — the
series is part of the module's identity and the wrong one is worse than a
stopped commit.

A four-part version whose first two parts are not an Odoo series (e.g.
`1.2.3.4`) is not a valid version in any series and is rejected.

Bump logic (derived from commits since main/master):
    BREAKING CHANGE footer or <type>!  → major (resets minor + patch to 0)
    feat                               → minor (resets patch to 0)
    anything else                      → patch
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

# An Odoo series is <major>.0 — 8.0 is the oldest thing anyone still ports, and
# the floor is what keeps `1.2.3.4` from reading as series 1.2 with a remainder.
SERIES_RE = re.compile(r"^(\d+)\.0$")
MIN_SERIES_MAJOR = 8
# Odoo's own rule for what follows the series: two or three numeric parts.
REMAINDER_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")


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


def _is_series(first: str, second: str) -> bool:
    return (
        first.isdigit() and int(first) >= MIN_SERIES_MAJOR and second == "0"
    )


def _series_from_hint(hint: str, version: str) -> str:
    """The series for a version that carries none. Never guessed."""
    if not hint:
        raise ValueError(
            f"version {version!r} carries no Odoo series and ODOO_VERSION is "
            f"unset, so the series it belongs to is unknown. Re-run with "
            f"ODOO_VERSION=<NN.0> (the series this module targets, e.g. "
            f"ODOO_VERSION=18.0)"
        )
    if not SERIES_RE.match(hint):
        raise ValueError(
            f"version {version!r} carries no Odoo series and "
            f"ODOO_VERSION={hint!r} is not one. Set ODOO_VERSION=<NN.0> "
            f"(e.g. ODOO_VERSION=18.0)"
        )
    return hint


def parse_version(v: str, series_hint: str | None = None) -> tuple[str, int, int, int]:
    """Split any accepted Odoo version into (series, major, minor, patch).

    A missing trailing part is padded with 0, so every accepted form comes back
    in the same 4-tuple shape and `apply_bump` never sees a short version.
    """
    version = v.strip()
    parts = version.split(".")

    if len(parts) >= 4 and _is_series(parts[0], parts[1]):
        # <series>.A.B or <series>.A.B.C — the version says which series.
        series = f"{parts[0]}.{parts[1]}"
        remainder = ".".join(parts[2:])
    elif len(parts) >= 4:
        raise ValueError(
            f"{version!r} has {len(parts)} parts but does not start with an "
            f"Odoo series (<NN>.0, NN >= {MIN_SERIES_MAJOR}), so Odoo would "
            f"not accept it in any series. Expected A.B, A.B.C, "
            f"<series>.A.B or <series>.A.B.C"
        )
    else:
        # A.B / A.B.C — Odoo prepends the running series to these.
        series = _series_from_hint(series_hint or "", version)
        remainder = version
        if version.startswith(f"{series}."):
            # e.g. '18.0.1' under series 18.0: Odoo strips the series it finds
            # and the single part left is not a valid remainder.
            remainder = version[len(series) + 1:]

    if not REMAINDER_RE.match(remainder):
        raise ValueError(
            f"{version!r} is not a valid Odoo version: after the series, "
            f"{remainder!r} must be two or three numeric parts. Expected A.B, "
            f"A.B.C, <series>.A.B or <series>.A.B.C (e.g. 17.0.1.0.0)"
        )

    nums = [int(n) for n in remainder.split(".")]
    while len(nums) < 3:  # pad a missing C
        nums.append(0)
    return series, nums[0], nums[1], nums[2]


def canonical_version(v: str, series_hint: str | None = None) -> str:
    """The full 5-part `<series>.A.B.C` spelling of any accepted version."""
    series, major, minor, patch = parse_version(v, series_hint)
    return f"{series}.{major}.{minor}.{patch}"


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
    # The container's Odoo series, and the only source of a series for a
    # version that carries none.
    series_hint = os.environ.get("ODOO_VERSION", "").strip()
    try:
        normalized = canonical_version(current, series_hint)
    except ValueError as err:
        sys.exit(f"ERROR: {manifest_path}: {err}")

    commits = get_commits(module_path)
    if not commits:
        sys.exit("ERROR: No commits found")

    bump = determine_bump(commits)
    if normalized != current:
        # Canonical form first, bump second, so the manifest never holds a
        # bumped-but-still-short version.
        set_version(manifest_path, normalized)
        print(f"{current} → {normalized}  (normalized to <series>.A.B.C)")

    new_version = apply_bump(normalized, bump)
    set_version(manifest_path, new_version)
    print(f"{normalized} → {new_version}  ({bump})")


if __name__ == "__main__":
    main()

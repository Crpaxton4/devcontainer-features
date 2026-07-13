#!/usr/bin/env python3
"""Assert ``devcontainer-feature.json`` matches ``persisted-paths.tsv``.

``persisted-paths.tsv`` is the single source of truth for the Feature's
persisted bind-mount paths (see the header comment in that file). ``install.sh``
and ``setup.sh`` *derive* from it at runtime, so they cannot drift. The Feature
JSON is the one consumer that cannot be generated from the manifest:
release-please patches ``$.version`` into the checked-in JSON in place, so it
stays hand-authored. This check closes that gap - it fails CI whenever the
JSON's ``mounts`` or ``containerEnv`` no longer match the manifest rows.

Run as a CI step (``.github/workflows/test.yaml``) and exercised by
``test_check_persisted_paths.py`` under ``unittest discover``.

Like the other helpers here it is CI-only and stdlib-only: no ``odoo_sdk``, no
third-party TSV/JSON parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURE_DIR = REPO_ROOT / "devcontainer-features" / "src" / "personal-features"
MANIFEST = FEATURE_DIR / "persisted-paths.tsv"
FEATURE_JSON = FEATURE_DIR / "devcontainer-feature.json"

# Mount sources in the JSON are written host-home-relative behind this prefix so
# exactly one of HOME/USERPROFILE expands per host (#198). The manifest stores
# only the home-relative tail, so we re-add the prefix when comparing.
LOCAL_ENV_PREFIX = "${localEnv:HOME}${localEnv:USERPROFILE}/"

COLUMNS = ("name", "host_source", "container_target", "env_var", "env_value", "mode")
# Placeholder used in env_var/env_value for a row that has no container env var.
NONE = "-"


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """Parse the TSV into a list of row dicts, skipping comment/blank lines."""
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != len(COLUMNS):
            raise ValueError(
                f"{path}:{lineno}: expected {len(COLUMNS)} tab-separated columns "
                f"{COLUMNS}, got {len(fields)}: {line!r}"
            )
        row = dict(zip(COLUMNS, fields))
        # The trailing-slash dir/file marker drives whether install.sh / setup.sh
        # mkdir or touch a path, but this checker strips it before comparing to
        # the JSON - so a row that marks source and target inconsistently (dir on
        # one, file on the other) would pass the JSON check yet make the two
        # scripts disagree on what to create. A bind mount's source and target
        # are always the same kind, so require the markers to agree per row.
        if row["host_source"].endswith("/") != row["container_target"].endswith("/"):
            raise ValueError(
                f"{path}:{lineno}: host_source and container_target disagree on the "
                f"trailing-slash dir/file marker (both must be a directory or both a "
                f"file): {row['host_source']!r} vs {row['container_target']!r}"
            )
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no data rows found")
    return rows


def strip_dir_slash(path: str) -> str:
    """Drop the dir-marker trailing slash so a path compares to the JSON's."""
    return path[:-1] if path.endswith("/") and path != "/" else path


def host_source_paths(rows: list[dict]) -> set[str]:
    """Home-relative source path of each row, e.g. ``.config/gh`` - the same
    form as the Feature JSON's mount sources minus the localEnv prefix."""
    return {strip_dir_slash(r["host_source"]) for r in rows}


def expected_mounts(rows: list[dict]) -> list[dict]:
    """The ``mounts`` entries the Feature JSON must contain, per the manifest."""
    return [
        {
            "source": LOCAL_ENV_PREFIX + strip_dir_slash(r["host_source"]),
            "target": strip_dir_slash(r["container_target"]),
            "type": "bind",
        }
        for r in rows
    ]


def _mount_key(mount: dict) -> tuple:
    return (mount.get("source"), mount.get("target"), mount.get("type", "bind"))


def check(
    manifest_path: Path = MANIFEST, feature_json_path: Path = FEATURE_JSON
) -> list[str]:
    """Return a list of human-readable drift errors (empty when in sync)."""
    rows = load_manifest(manifest_path)
    feature = json.loads(feature_json_path.read_text())
    errors: list[str] = []

    # 1. Mounts must match the manifest exactly, in both directions.
    want = {_mount_key(m) for m in expected_mounts(rows)}
    got = {_mount_key(m) for m in feature.get("mounts", [])}
    for key in sorted(want - got):
        errors.append(f"mount declared by manifest is missing from JSON: {key}")
    for key in sorted(got - want):
        errors.append(f"mount in JSON is not declared by the manifest: {key}")

    # 2. Every manifest env var must be present in containerEnv with env_value.
    #    This is a subset check, not equality: the JSON legitimately carries
    #    non-path env vars (e.g. DISABLE_AUTOUPDATER, MEMPAL_DIR) that the
    #    manifest, which only tracks persisted paths, does not enumerate.
    container_env = feature.get("containerEnv", {})
    for row in rows:
        env_var = row["env_var"]
        if env_var == NONE:
            continue
        if env_var not in container_env:
            errors.append(
                f"containerEnv is missing {env_var!r}, declared by manifest row "
                f"{row['name']!r}"
            )
        elif container_env[env_var] != row["env_value"]:
            errors.append(
                f"containerEnv[{env_var!r}] is {container_env[env_var]!r}, but "
                f"manifest row {row['name']!r} says {row['env_value']!r}"
            )

    return errors


def main() -> int:
    errors = check()
    if errors:
        print(
            "persisted-paths.tsv and devcontainer-feature.json are out of sync:",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nUpdate persisted-paths.tsv and the Feature JSON together so they "
            "agree, then re-run this check.",
            file=sys.stderr,
        )
        return 1
    print("OK: devcontainer-feature.json matches persisted-paths.tsv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

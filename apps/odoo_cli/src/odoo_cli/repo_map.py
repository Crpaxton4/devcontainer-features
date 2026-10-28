"""``odoo-dev repo-map``: read and edit the project-to-repo map.

State maintenance runs before any config exists and carries no business logic,
so this module calls the data layer directly and never loads the logic layer.
Exit codes: 0 done, 3 nothing found, 4 the map is invalid or was refused.
"""

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from odoo_dev_data.home import ensure_home, resolve_home
from odoo_dev_data.repo_map import (
    Project,
    RepoMap,
    load_repo_map,
    resolve_project,
    save_repo_map,
    validate,
)

NOT_FOUND, INVALID = 3, 4


def _split(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    """Print ``data`` as JSON for ``--json``, else the human ``text``."""
    print(json.dumps(data, indent=2, sort_keys=True) if args.as_json else text)


def _project_line(project: Project) -> str:
    aliases = f"  aliases={','.join(project.aliases)}" if project.aliases else ""
    return f"{project.name}  {project.repo}  {project.odoo_version}{aliases}"


def _project_text(project: Project) -> str:
    return "\n".join(
        f"{key}: {value}" for key, value in dataclasses.asdict(project).items()
    )


def _list(args: argparse.Namespace, home: Path) -> int:
    projects = list(load_repo_map(home=home).projects.values())
    _emit(
        args,
        [dataclasses.asdict(p) for p in projects],
        "\n".join(_project_line(p) for p in projects),
    )
    return 0


def _get(args: argparse.Namespace, home: Path) -> int:
    project = load_repo_map(home=home).find(args.name)
    if project is None:
        print(f"no project {args.name!r}", file=sys.stderr)
        return NOT_FOUND
    _emit(args, dataclasses.asdict(project), _project_text(project))
    return 0


def _resolve(args: argparse.Namespace, home: Path) -> int:
    repos_dir = Path(args.repos_dir) if args.repos_dir else None
    found = resolve_project(args.query, repos_dir=repos_dir, home=home)
    data = {
        "project": dataclasses.asdict(found.project) if found.project else None,
        "repo_root": str(found.repo_root) if found.repo_root else None,
        "origin": found.origin,
        "warnings": list(found.warnings),
    }
    lines = [f"project: {found.project.name if found.project else '-'}"]
    lines += [f"repo_root: {found.repo_root or '-'}", f"origin: {found.origin or '-'}"]
    lines += [f"warning: {warning}" for warning in found.warnings]
    _emit(args, data, "\n".join(lines))
    return 0 if found.project else NOT_FOUND


def _save(repo_map: RepoMap, home: Path) -> bool:
    """Write the map; print each validation error to stderr and say if it failed."""
    try:
        ensure_home(home)
        save_repo_map(repo_map, home=home)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return False
    return True


def _upgrade_branches(pairs: list[str]) -> dict[str, str]:
    series_branch = (pair.partition("=") for pair in pairs)
    return {series.strip(): branch.strip() for series, _, branch in series_branch}


def _set(args: argparse.Namespace, home: Path) -> int:
    if any("=" not in pair for pair in args.upgrade_branch):
        print("--upgrade-branch needs SERIES=BRANCH", file=sys.stderr)
        return INVALID
    project = Project(
        name=args.name,
        repo=args.repo,
        odoo_version=args.odoo_version,
        aliases=_split(args.aliases),
        branch_flow=_split(args.branch_flow),
        upgrade_branch=_upgrade_branches(args.upgrade_branch),
        release_assignee=args.release_assignee,
        release_reviewer=args.release_reviewer,
    )
    repo_map = load_repo_map(home=home)
    repo_map.projects[args.name] = project
    if not _save(repo_map, home):
        return INVALID
    _emit(args, dataclasses.asdict(project), f"set {args.name}")
    return 0


def _remove(args: argparse.Namespace, home: Path) -> int:
    repo_map = load_repo_map(home=home)
    if repo_map.projects.pop(args.name, None) is None:
        print(f"no project {args.name!r}", file=sys.stderr)
        return NOT_FOUND
    if not _save(repo_map, home):
        return INVALID
    _emit(args, {"removed": args.name}, f"removed {args.name}")
    return 0


def _validate(args: argparse.Namespace, home: Path) -> int:
    try:
        errors = validate(load_repo_map(home=home))
    except ValueError as exc:
        errors = [f"repo-map.json: {exc}"]
    _emit(args, {"ok": not errors, "errors": errors}, "\n".join(errors) or "ok")
    return INVALID if errors else 0


def _defaults(args: argparse.Namespace, home: Path) -> int:
    repo_map = load_repo_map(home=home)
    changes: dict[str, Any] = {}
    if args.branch_flow is not None:
        changes["branch_flow"] = _split(args.branch_flow)
    for key in ("release_assignee", "release_reviewer"):
        if getattr(args, key) is not None:
            changes[key] = getattr(args, key)
    if changes:
        repo_map.defaults = dataclasses.replace(repo_map.defaults, **changes)
        if not _save(repo_map, home):
            return INVALID
    data = dataclasses.asdict(repo_map.defaults)
    _emit(args, data, "\n".join(f"{key}: {value}" for key, value in data.items()))
    return 0


_VERBS: dict[str, Callable[[argparse.Namespace, Path], int]] = {
    "list": _list,
    "get": _get,
    "resolve": _resolve,
    "set": _set,
    "remove": _remove,
    "validate": _validate,
    "defaults": _defaults,
}


def run_repo_map(args: argparse.Namespace) -> int:
    """Run one ``repo-map`` verb against the map under ``ODOO_DEV_HOME``."""
    return _VERBS[args.verb](args, resolve_home(os.environ))

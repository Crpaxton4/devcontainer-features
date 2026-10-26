"""The ONE parser, validator, writer and resolver of ``repo-map.json``.

The file lives at ``paths(home).repo_map`` and has this schema::

    {"defaults": {"branch_flow": ["task", "E2E", "UAT", "main"],
                  "release_assignee": "", "release_reviewer": ""},
     "projects": {"<project name>": {
         "repo": "<folder under the repos dir, or an absolute path>",
         "odoo_version": "17.0", "aliases": [], "branch_flow": [],
         "upgrade_branch": {"19.0": "19-upgrade"},
         "release_assignee": "", "release_reviewer": ""}}}

The first flow element ``"task"`` stands for the per-task branch. The legacy
spelling ``":task"`` is read as ``"task"``. An empty project ``branch_flow``
means "use ``defaults.branch_flow``". The last element of a flow is the
long-lived branch that work ends on (the derived default branch).

Removed keys: ``repo_path`` (an absolute ``repo`` replaces it),
``default_branch`` (derived), ``flow_confirmed``, ``remote``, ``notes`` and
``_doc``. The reader tolerates them and unknown keys and drops them.

Validation runs on WRITE only (``save_repo_map``). The reader never rejects a
map because of its content, only because it is not a JSON object.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Mapping

from .home import paths

TASK_BRANCH = "task"
LEGACY_TASK_BRANCH = ":task"
DEFAULT_FLOW = ("task", "E2E", "UAT", "main")
SERIES = re.compile(r"^\d+\.0$")

_BAD_REF_CHARS = re.compile(r"[\x00-\x20~^:?*\[\\\x7f]")


@dataclass(frozen=True)
class Defaults:
    """Values a project inherits when it sets none."""

    branch_flow: tuple[str, ...] = DEFAULT_FLOW
    release_assignee: str = ""
    release_reviewer: str = ""


@dataclass(frozen=True)
class Project:
    """One Odoo project and the repository that holds its code."""

    name: str
    repo: str
    odoo_version: str
    aliases: tuple[str, ...] = ()
    branch_flow: tuple[str, ...] = ()
    upgrade_branch: Mapping[str, str] = field(default_factory=dict)
    release_assignee: str = ""
    release_reviewer: str = ""

    @property
    def repo_is_absolute(self) -> bool:
        return Path(self.repo).is_absolute()

    @property
    def repo_name(self) -> str:
        """The basename of ``repo``, which is also the GitHub repository name."""
        return Path(self.repo).name


@dataclass
class RepoMap:
    """The whole map plus the lookups every caller needs."""

    defaults: Defaults = field(default_factory=Defaults)
    projects: dict[str, Project] = field(default_factory=dict)

    def flow(self, name: str) -> tuple[str, ...]:
        """The project's branch flow, or the default flow when it sets none."""
        return self.projects[name].branch_flow or self.defaults.branch_flow

    def default_branch(self, name: str) -> str:
        """The long-lived branch work ends on: the last flow element."""
        return self.flow(name)[-1]

    def next_branch(
        self, name: str, current: str, existing: Collection[str] | None = None
    ) -> str | None:
        """The flow element after ``current``, skipping branches not in ``existing``.

        ``"task"`` is never returned. ``None`` at the end of the flow or when
        ``current`` is not in the flow.
        """
        flow = self.flow(name)
        if current not in flow:
            return None
        for branch in flow[flow.index(current) + 1 :]:
            if branch == TASK_BRANCH:
                continue
            if existing is None or branch in existing:
                return branch
        return None

    def base_branch(
        self, name: str, existing: Collection[str] | None = None
    ) -> str | None:
        """The branch a task branch is cut from and its PR targets."""
        return self.next_branch(name, TASK_BRANCH, existing)

    def release_assignee(self, name: str) -> str:
        return self.projects[name].release_assignee or self.defaults.release_assignee

    def release_reviewer(self, name: str) -> str:
        return self.projects[name].release_reviewer or self.defaults.release_reviewer

    def upgrade_branch(self, name: str, series: str) -> str:
        """The branch an upgrade to ``series`` is based on."""
        return self.projects[name].upgrade_branch.get(series) or self.default_branch(
            name
        )

    def find(self, query: str) -> Project | None:
        """Exact name, exact alias, then the same two case-insensitively."""
        projects = self.projects.values()
        lowered = query.lower()
        matchers = (
            lambda p: p.name == query,
            lambda p: query in p.aliases,
            lambda p: p.name.lower() == lowered,
            lambda p: lowered in (a.lower() for a in p.aliases),
        )
        for matches in matchers:
            for project in projects:
                if matches(project):
                    return project
        return None

    def by_repo_name(self, repo_name: str) -> list[Project]:
        """Projects whose ``repo_name`` equals ``repo_name``, ignoring case."""
        lowered = repo_name.lower()
        return [p for p in self.projects.values() if p.repo_name.lower() == lowered]


# --- parse and serialise ---------------------------------------------------


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _flow(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        TASK_BRANCH if item == LEGACY_TASK_BRANCH else str(item) for item in value
    )


def _project(name: str, entry: Any) -> Project:
    if not isinstance(entry, dict):
        raise ValueError(f"{name}: entry must be an object")
    legacy_path = _str(entry.get("repo_path"))
    aliases = entry.get("aliases")
    upgrade = entry.get("upgrade_branch")
    return Project(
        name=name,
        repo=legacy_path or _str(entry.get("repo")),
        odoo_version=_str(entry.get("odoo_version")),
        aliases=tuple(str(a) for a in aliases) if isinstance(aliases, list) else (),
        branch_flow=_flow(entry.get("branch_flow")),
        upgrade_branch=(
            {str(k): str(v) for k, v in upgrade.items()}
            if isinstance(upgrade, dict)
            else {}
        ),
        release_assignee=_str(entry.get("release_assignee")),
        release_reviewer=_str(entry.get("release_reviewer")),
    )


def from_json(text: str) -> RepoMap:
    """Parse a map, tolerating unknown and legacy keys. Raise ``ValueError``."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("repo map must be a JSON object")
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError("projects must be an object")
    raw = data.get("defaults")
    raw = raw if isinstance(raw, dict) else {}
    defaults = Defaults(
        branch_flow=_flow(raw.get("branch_flow")) or DEFAULT_FLOW,
        release_assignee=_str(raw.get("release_assignee")),
        release_reviewer=_str(raw.get("release_reviewer")),
    )
    return RepoMap(
        defaults=defaults,
        projects={name: _project(name, entry) for name, entry in projects.items()},
    )


def to_json(repo_map: RepoMap) -> str:
    """Serialise in the new schema only: sorted keys, indent 2, final newline."""
    defaults = repo_map.defaults
    document = {
        "defaults": {
            "branch_flow": list(defaults.branch_flow),
            "release_assignee": defaults.release_assignee,
            "release_reviewer": defaults.release_reviewer,
        },
        "projects": {
            name: {
                "repo": p.repo,
                "odoo_version": p.odoo_version,
                "aliases": list(p.aliases),
                "branch_flow": list(p.branch_flow),
                "upgrade_branch": dict(p.upgrade_branch),
                "release_assignee": p.release_assignee,
                "release_reviewer": p.release_reviewer,
            }
            for name, p in repo_map.projects.items()
        },
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def load_repo_map(path: Path | None = None, *, home: Path | None = None) -> RepoMap:
    """Read the map. A missing file gives an empty map."""
    path = path or paths(home).repo_map
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RepoMap()
    return from_json(text)


# --- validate and save -----------------------------------------------------


def _ref_error(branch: str) -> str | None:
    """Port of ``refErr`` (git check-ref-format for one branch name)."""
    if not branch.strip():
        return "is empty"
    if branch.startswith("-") or _BAD_REF_CHARS.search(branch):
        return "contains a character git forbids in a branch name"
    if branch == "@" or ".." in branch or "@{" in branch or branch.endswith("."):
        return "is not a legal git branch name"
    if any(
        not s or s.startswith(".") or s.endswith(".lock") for s in branch.split("/")
    ):
        return "is not a legal git branch name"
    return None


def _flow_errors(label: str, flow: tuple[str, ...], minimum: int) -> list[str]:
    errors = []
    if 0 < len(flow) < 2 or (minimum and len(flow) < minimum):
        errors.append(f"{label}: branch_flow needs at least a source and a target")
    for index, branch in enumerate(flow):
        if branch == TASK_BRANCH:
            if index:
                errors.append(
                    f'{label}: "{TASK_BRANCH}" is only ever the first element of '
                    f"branch_flow, not element {index}"
                )
        elif problem := _ref_error(branch):
            errors.append(f'{label}: branch_flow entry "{branch}" {problem}')
    if len(set(flow)) != len(flow):
        errors.append(f"{label}: branch_flow repeats a branch")
    return errors


def _upgrade_errors(name: str, upgrade: Mapping[str, str]) -> list[str]:
    errors = []
    for series, branch in upgrade.items():
        if not SERIES.match(series):
            errors.append(f'{name}: upgrade_branch key "{series}" must be like 19.0')
        if branch == TASK_BRANCH or _ref_error(branch):
            errors.append(
                f'{name}: upgrade_branch["{series}"] "{branch}" '
                "is not a legal git branch name"
            )
    return errors


def _alias_errors(repo_map: RepoMap) -> list[str]:
    errors = []
    owner: dict[str, str] = {}
    for name, project in repo_map.projects.items():
        for alias in project.aliases:
            if not alias.strip():
                errors.append(f"{name}: aliases entries must be non-empty strings")
            elif alias in repo_map.projects:
                errors.append(f'{name}: alias "{alias}" is also the name of a project')
            elif alias in owner:
                errors.append(
                    f'{name}: alias "{alias}" is already an alias of "{owner[alias]}"'
                )
            else:
                owner[alias] = name
    return errors


def _project_errors(name: str, project: Project) -> list[str]:
    errors = []
    if not name.strip():
        errors.append(f"{name}: empty project name")
    if not project.repo.strip():
        errors.append(f"{name}: repo is required")
    if not SERIES.match(project.odoo_version):
        errors.append(
            f'{name}: odoo_version "{project.odoo_version}" must be like 17.0'
        )
    errors += _flow_errors(name, project.branch_flow, 0)
    return errors + _upgrade_errors(name, project.upgrade_branch)


def validate(repo_map: RepoMap) -> list[str]:
    """Return ``"<project>: <message>"`` errors. An empty list means valid."""
    errors = _flow_errors("defaults", repo_map.defaults.branch_flow, 2)
    for name, project in repo_map.projects.items():
        errors += _project_errors(name, project)
    return errors + _alias_errors(repo_map)


def save_repo_map(
    repo_map: RepoMap, path: Path | None = None, *, home: Path | None = None
) -> Path:
    """Validate, back up an existing file to ``<path>.bak`` and write atomically.

    Raise ``ValueError`` for an invalid map and ``FileNotFoundError`` when the
    parent directory is missing. ``ensure_home`` makes the directory.
    """
    path = path or paths(home).repo_map
    errors = validate(repo_map)
    if errors:
        raise ValueError("\n".join(errors))
    if not path.parent.is_dir():
        raise FileNotFoundError(f"no such directory: {path.parent}")
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(to_json(repo_map))
    os.replace(handle.name, path)
    return path


# --- resolve ---------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """The outcome of ``resolve_project``."""

    project: Project | None
    repo_root: Path | None
    origin: str | None
    warnings: tuple[str, ...]


def _git(cwd: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip() or None


def _owner_repo(url: str | None) -> str | None:
    """Reduce a remote URL to ``owner/repo``."""
    if not url:
        return None
    parts = [p for p in re.split(r"[/:]", url.removesuffix(".git")) if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def _checkout(path: Path) -> tuple[Path | None, str | None]:
    """The checkout root containing ``path`` and its ``owner/repo`` origin."""
    top = _git(path, "rev-parse", "--show-toplevel")
    if top is None:
        return None, None
    return Path(top), _owner_repo(_git(Path(top), "remote", "get-url", "origin"))


def _repo_part(origin: str | None) -> str | None:
    return origin.split("/")[-1] if origin else None


def _pick_project(
    repo_map: RepoMap, query: str | None, fallback_name: str | None
) -> tuple[Project | None, str | None]:
    """Return the project, or ``None`` plus the warning that explains why."""
    candidates: list[Project] = []
    if query:
        found = repo_map.find(query)
        if found:
            return found, None
    if fallback_name and not query:
        candidates = repo_map.by_repo_name(fallback_name)
    elif query:
        candidates = repo_map.by_repo_name(query)
    if len(candidates) == 1:
        return candidates[0], None
    if candidates:
        names = ", ".join(p.name for p in candidates)
        return None, f"ambiguous: {names}; name the project"
    return None, f"no repo-map entry for {query or fallback_name}"


def _absolute_root(project: Project, warnings: list[str]) -> Path | None:
    """The stored checkout, unless it belongs to another repository."""
    path = Path(project.repo)
    root, origin = _checkout(path)
    if root is None:
        warnings.append(f"{path} is not a git checkout; ignored")
        return None
    name = _repo_part(origin)
    if name is not None and name.lower() != project.repo_name.lower():
        warnings.append(
            f"{path} is checked out from {origin}, not {project.repo_name}; ignored"
        )
        return None
    return path


def _project_root(
    project: Project,
    checkout_root: Path | None,
    origin_name: str | None,
    repos_dir: Path | None,
    warnings: list[str],
) -> Path | None:
    if project.repo_is_absolute:
        root = _absolute_root(project, warnings)
        if root is not None:
            return root
    if repos_dir is not None:
        candidate = repos_dir / project.repo_name
        if candidate.is_dir():
            return candidate
    if checkout_root is not None:
        wanted = project.repo_name.lower()
        if (
            checkout_root.name.lower() == wanted
            or (origin_name or "").lower() == wanted
        ):
            return checkout_root
    return None


def resolve_project(
    query: str | None = None,
    *,
    cwd: Path | None = None,
    repos_dir: Path | None = None,
    repo_map: RepoMap | None = None,
    home: Path | None = None,
) -> Resolved:
    """Resolve a project name, alias or the current checkout to a project and root.

    A stored absolute ``repo`` is ignored, with a warning, when that checkout's
    ``origin`` is another repository.
    """
    repo_map = load_repo_map(home=home) if repo_map is None else repo_map
    checkout_root, origin = _checkout(cwd or Path.cwd())
    origin_name = _repo_part(origin)
    fallback = origin_name or (checkout_root.name if checkout_root else None)
    project, warning = _pick_project(repo_map, query, fallback)
    warnings = [warning] if warning else []
    if project is None:
        return Resolved(None, checkout_root, origin, tuple(warnings))
    root = _project_root(project, checkout_root, origin_name, repos_dir, warnings)
    return Resolved(project, root, origin, tuple(warnings))

"""``odoo-sdk sync-skills``: materialize the packaged skills into a directory.

Two mutually exclusive modes, one per flag (#714):

* ``--target-dir <dir>`` — serve-and-sync: an in-process
  :class:`~odoo_sdk.mcp.server.OdooMCPServer` serves the packaged skills over
  MCP resources, an in-memory ``fastmcp.Client`` connects to it, and fastmcp's
  ``sync_skills`` utility downloads every served skill into ``<dir>``. This is
  the ad-hoc install path for non-Claude consumers: what lands on disk is
  exactly what an MCP client would sync from the running server.
* ``--dest <dir>`` — direct copy: each owned skill directory is copied
  byte-for-byte from the package data (:func:`odoo_sdk.skills.skills_root`)
  into ``<dir>`` with no MCP machinery involved. The plugin skill-parity
  regeneration workflow runs this as
  ``odoo-sdk sync-skills --dest plugins/odoo-dev/skills``, so the result is
  deterministic and byte-stable.

Both modes share delete-then-copy semantics: before writing, only the *owned*
skill directories (:data:`odoo_sdk.skills.PACKAGED_SKILL_NAMES`) are removed
from the destination — ``overwrite=True`` alone would leave files that were
removed upstream lying around as stale content. Anything else in the
destination (non-owned skill directories, loose files) survives untouched.

After a ``--target-dir`` sync, every synced file under a ``scripts/``
subdirectory is ``chmod 0o755`` — the MCP download path writes plain 0644,
which would strip the executable bit skills rely on. The ``--dest`` copy
preserves the package data's modes, so no chmod pass is needed there.

Success prints exactly one JSON summary document (skills synced, files
written, deletions, executables restored) to stdout. A usage error — both
flags, or neither — prints the shared dispatch error envelope (the same
``{"error": {"type", "message"}}`` shape the ``cmd`` subcommand renders) and
exits 2.

The ``--target-dir`` path imports :class:`OdooMCPServer` from
``odoo_sdk.mcp.server`` lazily (function-local): the mode is *defined* as
"what an MCP client would sync from the running server", so building the
server in-process is the point. This is a named ADR-005 exception — the
``cli``/``mcp`` independence contract carries an explicit ``ignore_imports``
entry for it — and the lazy import keeps fastmcp's multi-second import cost
off the ``--dest`` parity gate entirely.
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any, NoReturn, Optional

from odoo_sdk.commands import Registry
from odoo_sdk.commands.dispatch_telemetry import _error_payload
from odoo_sdk.skills import PACKAGED_SKILL_NAMES, skills_root

__all__ = ["cmd_sync_skills", "sync_via_server", "copy_from_package"]

#: Mode restored on synced files under a ``scripts/`` subdirectory. The MCP
#: download path writes every file 0644; skill scripts must stay executable.
SCRIPT_FILE_MODE = 0o755


class _NullRpcClient:
    """Structurally satisfy ``RpcClient`` while refusing every RPC.

    The in-process server built for ``--target-dir`` serves only skill
    resources — no tool ever dispatches — so the registry's client must never
    be exercised. Raising loudly (instead of handing over a real
    :class:`~odoo_sdk.client.OdooClient`) keeps the mode's contract explicit:
    syncing skills needs no Odoo connection or configuration at all.
    """

    def _refuse(self) -> NoReturn:
        raise RuntimeError("sync-skills never talks to Odoo")

    @property
    def uid(self) -> int:
        self._refuse()

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self._refuse()

    def __getitem__(self, model_name: str) -> Any:
        self._refuse()


def _delete_owned(target: Path) -> list[str]:
    """Remove every *owned* skill entry from ``target``; return what was removed.

    Only names in :data:`PACKAGED_SKILL_NAMES` are touched (a directory is
    removed recursively; a stray file or symlink squatting on an owned name is
    unlinked), so non-owned skill directories and loose files survive. This is
    the delete half of delete-then-copy: a file removed upstream must not
    linger in the destination just because ``overwrite=True`` never rewrites it.

    :param target: Destination directory being synced into.
    :return: Sorted owned names that existed and were removed.
    """
    deleted = []
    for name in PACKAGED_SKILL_NAMES:
        path = target / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            deleted.append(name)
        elif path.exists() or path.is_symlink():
            path.unlink()
            deleted.append(name)
    return sorted(deleted)


def _relative_files(skill_dir: Path) -> list[str]:
    """Return the sorted relative POSIX paths of every file under ``skill_dir``."""
    return sorted(
        path.relative_to(skill_dir).as_posix()
        for path in skill_dir.rglob("*")
        if path.is_file()
    )


def _chmod_scripts(skill_dir: Path) -> list[str]:
    """Restore :data:`SCRIPT_FILE_MODE` on files under any ``scripts/`` subdir.

    The MCP download path writes every file 0644; this pass re-marks the files
    that live under a ``scripts/`` directory (at any depth inside the skill) as
    executable.

    :param skill_dir: One synced skill directory.
    :return: Sorted relative POSIX paths of the files whose mode was set.
    """
    restored = []
    for rel in _relative_files(skill_dir):
        if "scripts" in Path(rel).parts[:-1]:
            (skill_dir / rel).chmod(SCRIPT_FILE_MODE)
            restored.append(rel)
    return restored


def _summary(
    mode: str,
    target: Path,
    skill_dirs: list[Path],
    deleted: list[str],
    executable: list[str],
) -> dict:
    """Build the JSON summary document printed on success (all lists sorted)."""
    skill_dirs = sorted(skill_dirs)
    return {
        "mode": mode,
        "target": str(target),
        "skills": [skill_dir.name for skill_dir in skill_dirs],
        "deleted": deleted,
        "files_written": sorted(
            f"{skill_dir.name}/{rel}"
            for skill_dir in skill_dirs
            for rel in _relative_files(skill_dir)
        ),
        "executable": executable,
    }


async def _download_all(server: Any, target: Path) -> list[Path]:
    """Sync every skill the in-process ``server`` serves into ``target``."""
    from fastmcp import Client
    from fastmcp.utilities.skills import sync_skills

    async with Client(server.mcp) as client:
        return await sync_skills(client, target, overwrite=True)


def sync_via_server(target_dir: str, skills_source: Optional[Path] = None) -> dict:
    """Serve-and-sync (``--target-dir``): download the skills over in-memory MCP.

    Builds the lightest valid :class:`OdooMCPServer` — a bare
    :class:`~odoo_sdk.commands.Registry` over a :class:`_NullRpcClient`, no
    explicit tools, skills serving on — connects ``fastmcp.Client`` to it
    in-memory, and syncs every served skill into ``target_dir`` with
    delete-then-copy semantics for the owned names, then restores the
    executable bit under ``scripts/``.

    :param target_dir: Directory to sync the skills into (created if absent).
    :param skills_source: Skill-folder root to serve instead of the packaged
        :func:`odoo_sdk.skills.skills_root` (tests use this to serve fixture
        skills). Defaults to None (serve the packaged skills).
    :return: The JSON-ready summary dict.
    """
    # Named ADR-005 exception (#714): the cli/mcp independence contract
    # carries an ignore_imports entry for exactly this import. Lazy so the
    # --dest path (the CI parity gate) never pays fastmcp's import cost.
    from odoo_sdk.mcp.server import OdooMCPServer

    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    deleted = _delete_owned(target)
    server = OdooMCPServer(
        Registry(_NullRpcClient()),
        serve_skills=True,
        skills_root=skills_source,
    )
    synced = asyncio.run(_download_all(server, target))
    executable = sorted(
        f"{skill_dir.name}/{rel}"
        for skill_dir in synced
        for rel in _chmod_scripts(skill_dir)
    )
    return _summary("target-dir", target, synced, deleted, executable)


def copy_from_package(dest_dir: str) -> dict:
    """Direct copy (``--dest``): byte-identical owned skill dirs from package data.

    No MCP machinery: each owned skill directory is copied verbatim from
    :func:`odoo_sdk.skills.skills_root` after the owned names are deleted from
    the destination, so the result is deterministic and byte-stable — the
    contract the plugin skill-parity regeneration gate relies on. File modes
    are preserved by the copy, so no chmod pass runs.

    :param dest_dir: Destination tree (created if absent).
    :return: The JSON-ready summary dict.
    """
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    deleted = _delete_owned(dest)
    source = skills_root()
    synced = [
        Path(shutil.copytree(source / name, dest / name))
        for name in PACKAGED_SKILL_NAMES
    ]
    return _summary("dest", dest, synced, deleted, executable=[])


def _usage_fail(message: str) -> NoReturn:
    """Print the shared dispatch error envelope on stdout and exit 2.

    The envelope is built by the same
    :func:`~odoo_sdk.commands.dispatch_telemetry._error_payload` the ``cmd``
    dispatcher and the MCP error boundary use, so every surface renders usage
    failures as one ``{"error": {"type", "message"}}`` shape.
    """
    print(json.dumps(_error_payload(ValueError(message)), default=str))
    sys.exit(2)


def cmd_sync_skills(args: argparse.Namespace) -> None:
    """Handle ``odoo-sdk sync-skills``: route to one mode, print one JSON doc.

    ``--target-dir`` and ``--dest`` are mutually exclusive and exactly one is
    required; violating that is a usage error (exit 2, shared envelope).
    """
    if (args.target_dir is None) == (args.dest is None):
        _usage_fail("exactly one of --target-dir or --dest is required")
    if args.target_dir is not None:
        summary = sync_via_server(args.target_dir)
    else:
        summary = copy_from_package(args.dest)
    print(json.dumps(summary))

"""Tests for ``odoo-sdk sync-skills`` (#714).

The command materializes the packaged consulting skills into a directory in
one of two mutually exclusive modes: ``--target-dir`` (serve-and-sync — an
in-process :class:`OdooMCPServer` serves the skills, an in-memory
``fastmcp.Client`` downloads them) and ``--dest`` (direct byte-identical copy
from package data, the deterministic path the plugin skill-parity gate runs).
Both share delete-then-copy semantics for the *owned* names
(:data:`~odoo_sdk.skills.PACKAGED_SKILL_NAMES`): stale files inside an owned
skill directory disappear on re-sync, while non-owned directories and loose
files in the destination survive untouched. After a ``--target-dir`` sync,
files under a ``scripts/`` subdirectory are re-marked 0o755 (the MCP download
path writes 0644). Usage errors (neither flag, or both) exit 2 with the same
``{"error": {"type", "message"}}`` envelope the ``cmd`` dispatcher renders.
"""

import json
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.cli.sync_skills import (
    SCRIPT_FILE_MODE,
    _chmod_scripts,
    _NullRpcClient,
    copy_from_package,
    sync_via_server,
)
from odoo_sdk.skills import PACKAGED_SKILL_NAMES, skills_root


def _run_cli(*argv: str) -> tuple[int, str]:
    """Run ``cli.main()`` with ``argv``; return ``(exit_code, stdout)``."""
    out = StringIO()
    code = 0
    with patch.object(sys, "argv", ["odoo-sdk", *argv]), redirect_stdout(out):
        try:
            cli.main()
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue()


def _tree_files(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` (relative POSIX path) to its bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _packaged_tree() -> dict[str, bytes]:
    """The package-data ground truth: every owned skill's files, by rel path."""
    combined: dict[str, bytes] = {}
    root = skills_root()
    for name in PACKAGED_SKILL_NAMES:
        for rel, content in _tree_files(root / name).items():
            combined[f"{name}/{rel}"] = content
    return combined


def _seed_non_owned(target: Path) -> None:
    """Plant a non-owned skill dir and a loose file that must survive a sync."""
    foreign = target / "somebody-elses-skill"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("not ours\n")
    (target / "README.md").write_text("loose file\n")


def _assert_non_owned_survived(test: unittest.TestCase, target: Path) -> None:
    test.assertEqual(
        (target / "somebody-elses-skill" / "SKILL.md").read_text(), "not ours\n"
    )
    test.assertEqual((target / "README.md").read_text(), "loose file\n")


def _assert_owned_tree_byte_equal(test: unittest.TestCase, target: Path) -> None:
    """Assert the owned portion of ``target`` equals the package data exactly."""
    packaged = _packaged_tree()
    synced = {
        rel: content
        for rel, content in _tree_files(target).items()
        if rel.split("/", 1)[0] in PACKAGED_SKILL_NAMES
    }
    test.assertEqual(synced, packaged)


class SyncSkillsTargetDirMode(unittest.TestCase):
    """The serve-and-sync mode: in-process server, in-memory client."""

    def test_sync_into_empty_dir_is_byte_equal_to_package_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            code, out = _run_cli("sync-skills", "--target-dir", str(target))
            self.assertEqual(code, 0)
            summary = json.loads(out)
            self.assertEqual(summary["mode"], "target-dir")
            self.assertEqual(summary["skills"], sorted(PACKAGED_SKILL_NAMES))
            self.assertEqual(summary["deleted"], [])
            self.assertEqual(summary["files_written"], sorted(_packaged_tree().keys()))
            _assert_owned_tree_byte_equal(self, target)

    def test_stale_file_in_owned_dir_disappears_on_resync(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            stale = target / PACKAGED_SKILL_NAMES[0] / "removed-upstream.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale\n")
            code, out = _run_cli("sync-skills", "--target-dir", str(target))
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())
            self.assertEqual(json.loads(out)["deleted"], [PACKAGED_SKILL_NAMES[0]])
            _assert_owned_tree_byte_equal(self, target)

    def test_non_owned_dir_and_loose_file_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            _seed_non_owned(target)
            code, _ = _run_cli("sync-skills", "--target-dir", str(target))
            self.assertEqual(code, 0)
            _assert_non_owned_survived(self, target)

    def test_file_squatting_on_an_owned_name_is_unlinked(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / PACKAGED_SKILL_NAMES[1]).write_text("a file, not a dir\n")
            code, out = _run_cli("sync-skills", "--target-dir", str(target))
            self.assertEqual(code, 0)
            self.assertTrue((target / PACKAGED_SKILL_NAMES[1]).is_dir())
            self.assertEqual(json.loads(out)["deleted"], [PACKAGED_SKILL_NAMES[1]])


class SyncSkillsChmodScripts(unittest.TestCase):
    """Files under scripts/ are re-marked 0o755 after a --target-dir sync.

    The five packaged skills carry no supporting files today, so the sync path
    is exercised through the provider over a fixture skills root that serves a
    skill *with* a ``scripts/`` file (plus the chmod helper directly).
    """

    @staticmethod
    def _fixture_root(tmp: Path) -> Path:
        root = tmp / "fixture-skills"
        skill = root / "fixture-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: fixture-skill\ndescription: A fixture.\n---\nBody.\n"
        )
        (skill / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")
        (skill / "reference.md").write_text("not a script\n")
        return root

    def test_synced_scripts_files_are_made_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            summary = sync_via_server(
                str(target), skills_source=self._fixture_root(Path(tmp))
            )
            script = target / "fixture-skill" / "scripts" / "run.sh"
            self.assertEqual(stat.S_IMODE(script.stat().st_mode), SCRIPT_FILE_MODE)
            self.assertEqual(summary["executable"], ["fixture-skill/scripts/run.sh"])
            # Non-script files stay at the sync's default (not executable).
            reference = target / "fixture-skill" / "reference.md"
            self.assertFalse(reference.stat().st_mode & stat.S_IXUSR)

    def test_chmod_helper_targets_only_files_under_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "some-skill"
            (skill / "scripts" / "nested").mkdir(parents=True)
            (skill / "SKILL.md").write_text("body\n")
            (skill / "scripts" / "a.sh").write_text("#!/bin/sh\n")
            (skill / "scripts" / "nested" / "b.py").write_text("print()\n")
            for path in (skill / "scripts" / "a.sh", skill / "SKILL.md"):
                path.chmod(0o644)
            restored = _chmod_scripts(skill)
            self.assertEqual(restored, ["scripts/a.sh", "scripts/nested/b.py"])
            for rel in restored:
                mode = stat.S_IMODE((skill / rel).stat().st_mode)
                self.assertEqual(mode, SCRIPT_FILE_MODE)
            self.assertEqual(stat.S_IMODE((skill / "SKILL.md").stat().st_mode), 0o644)


class SyncSkillsDestMode(unittest.TestCase):
    """The direct-copy mode: package data to destination, no MCP server."""

    def test_copy_into_empty_dir_is_byte_equal_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "plugins" / "odoo-dev" / "skills"
            code, out = _run_cli("sync-skills", "--dest", str(dest))
            self.assertEqual(code, 0)
            summary = json.loads(out)
            self.assertEqual(summary["mode"], "dest")
            self.assertEqual(summary["skills"], sorted(PACKAGED_SKILL_NAMES))
            _assert_owned_tree_byte_equal(self, dest)
            # Byte-stable: a second run over the same tree reports the same
            # summary (only re-listing what it deleted and rewrote).
            rerun = copy_from_package(str(dest))
            self.assertEqual(rerun["files_written"], summary["files_written"])
            self.assertEqual(rerun["deleted"], sorted(PACKAGED_SKILL_NAMES))
            _assert_owned_tree_byte_equal(self, dest)

    def test_delete_then_copy_and_non_owned_survival(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            _seed_non_owned(dest)
            stale = dest / PACKAGED_SKILL_NAMES[-1] / "stale.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale\n")
            code, _ = _run_cli("sync-skills", "--dest", str(dest))
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())
            _assert_non_owned_survived(self, dest)
            _assert_owned_tree_byte_equal(self, dest)


class SyncSkillsUsageErrors(unittest.TestCase):
    """Exactly one of --target-dir / --dest; violations render the envelope."""

    def _assert_usage_error(self, *argv: str) -> None:
        code, out = _run_cli(*argv)
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn(
            "exactly one of --target-dir or --dest", payload["error"]["message"]
        )

    def test_neither_flag_exits_2_with_envelope(self):
        self._assert_usage_error("sync-skills")

    def test_both_flags_exit_2_with_envelope(self):
        self._assert_usage_error("sync-skills", "--target-dir", "a", "--dest", "b")


class NullRpcClientRefusesRpc(unittest.TestCase):
    """The --target-dir server's registry client must never reach Odoo."""

    def test_every_rpc_member_raises(self):
        client = _NullRpcClient()
        with self.assertRaises(RuntimeError):
            client.uid
        with self.assertRaises(RuntimeError):
            client.execute("res.partner", "read")
        with self.assertRaises(RuntimeError):
            client["res.partner"]


if __name__ == "__main__":
    unittest.main()

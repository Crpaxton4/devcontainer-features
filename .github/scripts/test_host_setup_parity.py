"""Parity gate between the Feature's bind mounts and the host setup scripts.

A devcontainer bind mount whose source doesn't exist on the host is a hard
container-create failure, so ``setup.sh`` / ``setup.ps1`` must create *every*
mount source the Feature declares. That list used to be duplicated in four
places (the Feature JSON, ``setup.sh``, and ``.github/workflows/test.yaml``
twice) and predictably drifted: ``~/.config/odoo_sdk`` was a mount source that
``setup.sh`` never created.

``persisted-paths.tsv`` is now the single source of truth: ``setup.sh`` reads it
directly (so it cannot drift), and ``.github/scripts/check_persisted_paths.py``
gates the Feature JSON against it. ``setup.ps1`` is the one consumer that is
*hand-maintained* rather than derived - there is no Windows CI runner, so it
can't read the manifest at container-build time - so this gate's job is to keep
that hand-maintained list matching the manifest, plus confirm ``setup.sh`` still
reads the manifest rather than a hardcoded copy.

It also guards the two things issue #198 fixed, which are invisible to any
Linux test:

* every mount source must use the ``${localEnv:HOME}${localEnv:USERPROFILE}``
  concat pattern - with ``${localEnv:HOME}`` alone, sources expand to ``/.claude``
  on a native Windows host (which has USERPROFILE, not HOME) and the container
  fails to start;
* shell history must be mounted as a *directory*, because Docker Desktop
  materialises a missing single-file mount source as a directory and then fails
  the mount.

Like the other helpers here this is CI-only and stdlib-only: no ``odoo_sdk``, no
third-party YAML/JSON5 parser.
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURE_JSON = (
    REPO_ROOT
    / "devcontainer-features"
    / "src"
    / "personal-features"
    / "devcontainer-feature.json"
)
SETUP_SH = REPO_ROOT / "setup.sh"
SETUP_PS1 = REPO_ROOT / "setup.ps1"

LOCAL_ENV_PREFIX = "${localEnv:HOME}${localEnv:USERPROFILE}/"
SHELL_HISTORY_TARGET = "/usr/local/share/shell-history"


def _load_checker():
    """Load ``check_persisted_paths`` by path, matching test_mutation_gate."""
    spec = importlib.util.spec_from_file_location(
        "check_persisted_paths", SCRIPT_DIR / "check_persisted_paths.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _mounts():
    return json.loads(FEATURE_JSON.read_text())["mounts"]


def _mount_source_paths():
    """Home-relative source path of each mount, e.g. ``.config/gh``."""
    return {
        m["source"][len(LOCAL_ENV_PREFIX) :]
        for m in _mounts()
        if m["source"].startswith(LOCAL_ENV_PREFIX)
    }


def _manifest_source_paths():
    """Home-relative source path of each manifest row - what setup.sh creates."""
    return checker.host_source_paths(checker.load_manifest())


def _setup_ps1_paths():
    """Home-relative dirs created by setup.ps1, from its ``$paths = @(...)`` list."""
    body = SETUP_PS1.read_text()
    block = re.search(r"\$paths\s*=\s*@\((.*?)\)", body, re.DOTALL)
    assert block is not None, "setup.ps1 no longer declares a $paths = @(...) list"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _is_covered(source, created):
    """A mount source is covered if it, or a dir beneath it, gets created.

    ``setup.sh`` creates ``.config/pr-automation/projects``, which implicitly
    creates its ``.config/pr-automation`` parent - the actual mount source.
    """
    return any(p == source or p.startswith(source + "/") for p in created)


class TestMountSources(unittest.TestCase):
    def test_every_source_uses_the_home_userprofile_concat_pattern(self):
        # Regression guard for #198. Exactly one of HOME/USERPROFILE is defined
        # per host, so concatenating them yields a valid path everywhere.
        for mount in _mounts():
            with self.subTest(target=mount["target"]):
                self.assertTrue(
                    mount["source"].startswith(LOCAL_ENV_PREFIX),
                    f"mount source {mount['source']!r} must start with "
                    f"{LOCAL_ENV_PREFIX!r} or it breaks on native Windows hosts",
                )

    def test_shell_history_is_a_directory_mount(self):
        # Regression guard for #198: a single-file bind mount is materialised as
        # a *directory* by Docker Desktop when the source is missing, which then
        # fails the mount. Mount the containing directory instead.
        targets = [m["target"] for m in _mounts()]
        self.assertIn(SHELL_HISTORY_TARGET, targets)
        for target in targets:
            self.assertFalse(
                target.startswith(SHELL_HISTORY_TARGET + "/"),
                f"{target!r} mounts a file inside the shell-history dir; mount "
                f"{SHELL_HISTORY_TARGET!r} itself instead",
            )


class TestHostSetupParity(unittest.TestCase):
    def setUp(self):
        # Without this the per-source loops below pass vacuously if the prefix
        # ever changes and _mount_source_paths() comes back empty.
        self.assertTrue(_mount_source_paths(), "no mount sources were parsed")

    def test_manifest_covers_every_mount_source(self):
        # setup.sh reads the manifest, so covering every mount source is really a
        # property of the manifest. check_persisted_paths.py enforces exact
        # equality; this is the belt-and-braces "no mount left uncreated" view.
        created = _manifest_source_paths()
        for source in sorted(_mount_source_paths()):
            with self.subTest(source=source):
                self.assertTrue(
                    _is_covered(source, created),
                    f"the manifest never creates ~/{source}, so setup.sh leaves "
                    f"the bind mount to fail with 'bind source path does not exist'",
                )

    def test_setup_ps1_creates_every_mount_source(self):
        created = _setup_ps1_paths()
        for source in sorted(_mount_source_paths()):
            with self.subTest(source=source):
                self.assertTrue(
                    _is_covered(source, created),
                    f"setup.ps1 never creates ~/{source}, so the bind mount will "
                    f"fail on Windows hosts",
                )

    def test_setup_ps1_matches_manifest(self):
        # setup.ps1 is hand-maintained (no Windows CI runner reads the manifest
        # at build time), so this static check is the only thing keeping it from
        # drifting away from setup.sh's source of truth.
        self.assertEqual(_manifest_source_paths(), _setup_ps1_paths())

    def test_setup_sh_reads_the_manifest(self):
        # Guard against setup.sh being reverted to a hardcoded path list, which
        # would silently reintroduce the drift the manifest exists to prevent.
        self.assertIn("persisted-paths.tsv", SETUP_SH.read_text())

    def test_setup_scripts_do_not_create_history_files(self):
        # Both scripts used to `touch ~/.bash_history` (now a directory mount).
        # Creating a *file* where the Feature expects a directory is exactly the
        # #198 failure. Guard generically: no setup script may reference any
        # shell-history *file* path (one ending in `_history`), regardless of
        # which shell it belongs to.
        for script in (SETUP_SH, SETUP_PS1):
            with self.subTest(script=script.name):
                body = script.read_text()
                offenders = re.findall(r"\S*_history\b", body)
                self.assertEqual(
                    offenders,
                    [],
                    f"{script.name} references shell-history files {offenders}; "
                    f"history persistence is directory-based (see #198)",
                )


if __name__ == "__main__":
    unittest.main()

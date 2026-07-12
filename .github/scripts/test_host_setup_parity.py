"""Parity gate between the Feature's bind mounts and the host setup scripts.

A devcontainer bind mount whose source doesn't exist on the host is a hard
container-create failure, so ``setup.sh`` / ``setup.ps1`` must create *every*
mount source the Feature declares. That list used to be duplicated in four
places (the Feature JSON, ``setup.sh``, and ``.github/workflows/test.yaml``
twice) and predictably drifted: ``~/.config/odoo_sdk`` was a mount source that
``setup.sh`` never created. CI now runs ``setup.sh`` itself, which covers the
POSIX side - but there is no Windows runner, so ``setup.ps1`` can only be
checked statically. That's what this does.

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

import json
import re
import unittest
from pathlib import Path

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


def _mounts():
    return json.loads(FEATURE_JSON.read_text())["mounts"]


def _mount_source_paths():
    """Home-relative source path of each mount, e.g. ``.config/gh``."""
    return {
        m["source"][len(LOCAL_ENV_PREFIX) :]
        for m in _mounts()
        if m["source"].startswith(LOCAL_ENV_PREFIX)
    }


def _setup_sh_paths():
    """Home-relative dirs created by setup.sh, from its ``"$HOME/..."`` args."""
    return set(re.findall(r'"\$HOME/([^"]+)"', SETUP_SH.read_text()))


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

    def test_setup_sh_creates_every_mount_source(self):
        created = _setup_sh_paths()
        for source in sorted(_mount_source_paths()):
            with self.subTest(source=source):
                self.assertTrue(
                    _is_covered(source, created),
                    f"setup.sh never creates ~/{source}, so the bind mount will "
                    f"fail with 'bind source path does not exist'",
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

    def test_setup_scripts_agree(self):
        # There is no Windows runner, so this static check is the only thing
        # keeping the two scripts from drifting apart.
        self.assertEqual(_setup_sh_paths(), _setup_ps1_paths())

    def test_setup_scripts_do_not_create_history_files(self):
        # Both scripts used to `touch ~/.bash_history` (now a directory mount)
        # and `~/.zsh_history` (never mounted by anything). Creating a *file*
        # where the Feature expects a directory is exactly the #198 failure.
        for script in (SETUP_SH, SETUP_PS1):
            with self.subTest(script=script.name):
                body = script.read_text()
                self.assertNotIn(".bash_history", body)
                self.assertNotIn(".zsh_history", body)


if __name__ == "__main__":
    unittest.main()

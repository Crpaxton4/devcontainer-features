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

``TestSetupShOwnership`` is the one group here that *runs* ``setup.sh`` rather
than reading it: the #974 branch is a message and an exit status, and neither is
provable by grepping for a string that happens to be in the file.
"""

import importlib.util
import json
import os
import re
import subprocess
import tempfile
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
INSTALL_SH = (
    REPO_ROOT
    / "devcontainer-features"
    / "src"
    / "personal-features"
    / "install.sh"
)

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

    def test_host_provisioned_rows_are_not_created_by_install_sh(self):
        # A `provision=host` row is host-provisioned and ONLY ever a bind mount
        # (#369); install.sh must skip it, or a missing mount would be masked by
        # an empty container-created dir. The loop can't statically be executed
        # here, so assert install.sh's loop consumes the provision column and
        # short-circuits host rows.
        rows = checker.load_manifest()
        host_rows = [r for r in rows if r["provision"] == "host"]
        self.assertTrue(host_rows, "expected at least one host-provisioned row")
        body = INSTALL_SH.read_text()
        self.assertIn("_provision", body)
        self.assertRegex(
            body,
            r"host\)\s*continue",
            "install.sh must skip provision=host rows so the container never "
            "creates the host-provisioned target",
        )

    def test_setup_scripts_initialize_the_host_provisioned_database(self):
        # The host-provisioned tracker DB schema is created by the init script,
        # not the container (#369); both host setup scripts must invoke it.
        for script in (SETUP_SH, SETUP_PS1):
            with self.subTest(script=script.name):
                self.assertIn(
                    "init_tracker_db.py",
                    script.read_text(),
                    f"{script.name} must initialize the host-provisioned tracker "
                    f"database via scripts/init_tracker_db.py",
                )

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


# The stub `stat` the ownership tests put ahead of the real one on PATH. It
# answers the one question setup.sh's path_owner asks - "who owns this?" - with
# a uid nobody here can be, which is the only way to produce a foreign-owned
# directory without root. Anything else it refuses to answer, exactly as a stat
# that does not understand the flag would, so path_owner falls through to its
# "cannot tell" branch for paths that do not exist yet.
STAT_STUB = """#!/bin/sh
if [ "$1" = "-c" ] && [ "$2" = "%u" ] && [ -e "$3" ]; then
    echo 4242
    exit 0
fi
exit 1
"""


class TestSetupShOwnership(unittest.TestCase):
    """#974: a mount source setup.sh does not own stops it with the remedy.

    Docker does not refuse a bind mount whose source is missing on the host - it
    creates the source, as ``root:root 0755``. A row added to the manifest
    without a re-run of ``setup.sh`` therefore leaves a root-owned directory the
    container cannot write, and the only thing that ever reported it was
    ``setup.sh`` itself dying on ``chmod: Operation not permitted`` under
    ``set -eu``: no path, no cause, no fix.
    """

    def _run_setup(self, home, extra_path=None):
        env = dict(os.environ, HOME=str(home))
        if extra_path:
            env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            ["sh", str(SETUP_SH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_setup_sh_stops_on_a_source_it_does_not_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            # The first manifest row's source, pre-created so the guard has
            # something to look at. Everything else is still absent, which is
            # the ordinary case and must stay silent.
            first = checker.load_manifest()[0]
            (home / first["host_source"]).mkdir(parents=True)
            stub_dir = Path(tmp) / "stub-bin"
            stub_dir.mkdir()
            stub = stub_dir / "stat"
            stub.write_text(STAT_STUB)
            stub.chmod(0o755)

            result = self._run_setup(home, extra_path=stub_dir)
            message = result.stderr

            if os.getuid() == 0:
                # The other honest expectation rather than a skip: root may
                # chmod any path, so there is no failure ahead to pre-empt and
                # the guard must stand down instead of inventing one.
                self.assertEqual(
                    result.returncode,
                    0,
                    f"as root the ownership guard must stand down\nstdout:\n"
                    f"{result.stdout}\nstderr:\n{message}",
                )
                self.assertNotIn("sudo chown", result.stdout + message)
                return

            self.assertEqual(
                result.returncode,
                1,
                f"setup.sh must refuse a source it does not own; got "
                f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{message}",
            )
            self.assertIn("uid 4242", message)
            self.assertIn(first["name"], message)
            self.assertIn(str(home / first["host_source"]), message)
            # The remedy, in full: the chown nobody can guess, and the re-run
            # that applies the manifest's mode afterwards.
            self.assertIn(
                f"sudo chown -R {os.getuid()}:{os.getgid()} "
                f"{home / first['host_source']}",
                message,
            )
            self.assertIn("./setup.sh", message)
            self.assertIn("#974", message)
            # It stops AT the first offender, before touching anything, rather
            # than provisioning some rows and then dying on the chmod.
            self.assertEqual(result.stdout, "")

    def test_setup_sh_is_silent_over_sources_it_owns(self):
        # The inverse, and the guard against a check that fires on everything:
        # a clean host home must still provision end to end and say nothing
        # about ownership.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()

            result = self._run_setup(home)

            self.assertEqual(
                result.returncode,
                0,
                f"setup.sh failed over a home it owns\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}",
            )
            self.assertNotIn("sudo chown", result.stdout + result.stderr)
            for row in checker.load_manifest():
                with self.subTest(row=row["name"]):
                    self.assertTrue((home / row["host_source"]).exists())

    def test_setup_sh_still_never_chowns(self):
        # #974 prints a chown instruction; it must not start running one. This
        # script is deliberately unprivileged (it only ever touches paths under
        # $HOME), and acquiring root to repair a directory the user never asked
        # for is not its call. Allow the string inside the message, reject it as
        # a command.
        offenders = [
            line
            for line in SETUP_SH.read_text().splitlines()
            if re.match(r"\s*(sudo\s+)?chown\b", line)
        ]
        self.assertEqual(
            offenders,
            [],
            f"setup.sh executes chown {offenders}; it must only ever print the "
            f"instruction (#974)",
        )


class TestMountOwnershipCheckIsWired(unittest.TestCase):
    """#974: the runtime half has to be staged and invoked, or it reports nothing."""

    def test_install_sh_stages_the_manifest_into_the_image(self):
        body = INSTALL_SH.read_text()
        self.assertRegex(
            body,
            r"install -m 0644 \S+ "
            r"/usr/local/share/personal-features/persisted-paths\.tsv",
            "install.sh must stage persisted-paths.tsv into the image; without "
            "it check-mount-ownership has no rows to check at postCreate time "
            "(#974)",
        )

    def test_postcreate_runs_the_check_after_a_semicolon(self):
        # Not `&&`-chained, deliberately: an unwritable mount is a plausible
        # CAUSE of an earlier step in this chain failing (mempalace-repair
        # writing to the palace mount, for one), so the diagnostic has to run
        # precisely when the chain broke. It exits 0 by contract, so it cannot
        # mask a failure behind it either.
        post_create = json.loads(FEATURE_JSON.read_text())["postCreateCommand"]
        self.assertIn("check-mount-ownership", post_create)
        self.assertRegex(
            post_create,
            r";\s*check-mount-ownership\s*;",
            f"check-mount-ownership must sit between `;` boundaries so a failed "
            f"earlier step cannot skip it: {post_create!r}",
        )


if __name__ == "__main__":
    unittest.main()

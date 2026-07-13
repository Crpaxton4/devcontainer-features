"""Unit tests for the persisted-paths consistency gate (``check_persisted_paths.py``).

These exercise the drift-detection logic - that the checker accepts an in-sync
manifest/JSON pair and rejects each way the two can diverge (missing mount, extra
mount, wrong/absent env var) - plus an integration test asserting the *real*
repo files are actually in sync, which is what makes this catch drift under
``unittest discover``. The gate is a CI-only helper and must NOT depend on the
``odoo_sdk`` package, so this imports it directly by path from the same directory.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_persisted_paths", SCRIPT_DIR / "check_persisted_paths.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# A minimal, self-consistent manifest + JSON pair used by the synthetic tests.
MANIFEST_ROWS = [
    # name, host_source, container_target, env_var, env_value, mode
    ["alpha", ".alpha/", "/opt/alpha/", "ALPHA_DIR", "/opt/alpha", "0755"],
    ["beta", ".config/beta/", "/opt/beta/", "BETA_CONFIG", "/opt/beta/config.ini", "0755"],
    ["hist", ".config/hist/", "/opt/hist/", "-", "-", "0755"],
]

FEATURE_JSON = {
    "name": "Test",
    "version": "1.2.3",
    "containerEnv": {
        "ALPHA_DIR": "/opt/alpha",
        "BETA_CONFIG": "/opt/beta/config.ini",
        # A non-path env var the manifest does not (and should not) track.
        "UNRELATED": "1",
    },
    "mounts": [
        {"source": checker.LOCAL_ENV_PREFIX + ".alpha", "target": "/opt/alpha", "type": "bind"},
        {"source": checker.LOCAL_ENV_PREFIX + ".config/beta", "target": "/opt/beta", "type": "bind"},
        {"source": checker.LOCAL_ENV_PREFIX + ".config/hist", "target": "/opt/hist", "type": "bind"},
    ],
}


def _write_manifest(path: Path, rows):
    header = "# name\thost_source\tcontainer_target\tenv_var\tenv_value\tmode\n"
    body = "".join("\t".join(r) + "\n" for r in rows)
    path.write_text(header + body)


class TestLoadManifest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_parses_rows_and_skips_comments(self):
        path = self.dir / "m.tsv"
        _write_manifest(path, MANIFEST_ROWS)
        rows = checker.load_manifest(path)
        self.assertEqual(len(rows), len(MANIFEST_ROWS))
        self.assertEqual(rows[0]["name"], "alpha")
        self.assertEqual(rows[0]["env_var"], "ALPHA_DIR")
        self.assertEqual(rows[2]["env_var"], checker.NONE)

    def test_rejects_wrong_column_count(self):
        path = self.dir / "bad.tsv"
        path.write_text("# header\nalpha\t.alpha/\t/opt/alpha/\n")  # too few columns
        with self.assertRaises(ValueError):
            checker.load_manifest(path)

    def test_rejects_empty_manifest(self):
        path = self.dir / "empty.tsv"
        path.write_text("# only a comment\n")
        with self.assertRaises(ValueError):
            checker.load_manifest(path)

    def test_rejects_inconsistent_dir_file_marker(self):
        # host_source is a dir (trailing slash) but container_target is a file
        # (no slash): install.sh would touch a file while setup.sh mkdirs a dir.
        path = self.dir / "mixed.tsv"
        _write_manifest(
            path, [["x", ".x/", "/opt/x", "-", "-", "0755"]]
        )
        with self.assertRaises(ValueError):
            checker.load_manifest(path)


class TestHelpers(unittest.TestCase):
    def test_strip_dir_slash(self):
        self.assertEqual(checker.strip_dir_slash(".alpha/"), ".alpha")
        self.assertEqual(checker.strip_dir_slash(".bash_history"), ".bash_history")
        self.assertEqual(checker.strip_dir_slash("/"), "/")

    def test_host_source_paths_strip_trailing_slash(self):
        rows = [dict(zip(checker.COLUMNS, r)) for r in MANIFEST_ROWS]
        self.assertEqual(
            checker.host_source_paths(rows),
            {".alpha", ".config/beta", ".config/hist"},
        )


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.manifest = self.dir / "persisted-paths.tsv"
        self.json = self.dir / "devcontainer-feature.json"
        _write_manifest(self.manifest, MANIFEST_ROWS)
        self.json.write_text(json.dumps(FEATURE_JSON))

    def _check(self):
        return checker.check(self.manifest, self.json)

    def test_in_sync_pair_has_no_errors(self):
        self.assertEqual(self._check(), [])

    def test_detects_mount_missing_from_json(self):
        data = dict(FEATURE_JSON)
        data["mounts"] = FEATURE_JSON["mounts"][:-1]  # drop the hist mount
        self.json.write_text(json.dumps(data))
        errors = self._check()
        self.assertTrue(any("missing from JSON" in e for e in errors), errors)

    def test_detects_extra_mount_in_json(self):
        data = dict(FEATURE_JSON)
        data["mounts"] = FEATURE_JSON["mounts"] + [
            {"source": checker.LOCAL_ENV_PREFIX + ".extra", "target": "/opt/extra", "type": "bind"}
        ]
        self.json.write_text(json.dumps(data))
        errors = self._check()
        self.assertTrue(any("not declared by the manifest" in e for e in errors), errors)

    def test_detects_wrong_env_value(self):
        data = json.loads(json.dumps(FEATURE_JSON))
        data["containerEnv"]["BETA_CONFIG"] = "/opt/beta/WRONG.ini"
        self.json.write_text(json.dumps(data))
        errors = self._check()
        self.assertTrue(any("BETA_CONFIG" in e for e in errors), errors)

    def test_detects_missing_env_var(self):
        data = json.loads(json.dumps(FEATURE_JSON))
        del data["containerEnv"]["ALPHA_DIR"]
        self.json.write_text(json.dumps(data))
        errors = self._check()
        self.assertTrue(any("missing 'ALPHA_DIR'" in e for e in errors), errors)

    def test_env_mismatch_on_target_vs_env_value(self):
        # Regression guard: env_value is stored verbatim and need not equal the
        # mount target (an env var may point at a specific file inside the target
        # dir), so a check that confused the two would wrongly reject the files.
        data = json.loads(json.dumps(FEATURE_JSON))
        # Set BETA_CONFIG to the bare target dir instead of the config file.
        data["containerEnv"]["BETA_CONFIG"] = "/opt/beta"
        self.json.write_text(json.dumps(data))
        self.assertTrue(self._check())  # must flag the divergence


class TestRealRepoInSync(unittest.TestCase):
    """The real manifest and Feature JSON must agree - this is the drift guard."""

    def test_repo_is_in_sync(self):
        self.assertEqual(
            checker.check(),
            [],
            "persisted-paths.tsv and devcontainer-feature.json have drifted; "
            "run python .github/scripts/check_persisted_paths.py for details",
        )


if __name__ == "__main__":
    unittest.main()

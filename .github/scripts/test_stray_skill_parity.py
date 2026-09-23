"""Parity gate over the stray-skill name lists (#778).

The devcontainer feature used to publish six consulting skills as *loose*
directories under ``$CLAUDE_CONFIG_DIR/skills``. It stopped (#701-#708, #738):
five moved into the ``odoo-dev`` plugin, ``client-status-report`` was retired
outright (#700). That directory is a host bind mount, so the copies outlive the
image that wrote them, and two separate pieces of code now have to agree about
exactly which names are feature-seeded debris:

* ``install.sh`` (inside the ``sync-claude-mcp`` heredoc) **deletes** them;
* ``plugins/odoo-dev/scripts/check-stray-skills.sh`` **reports** them, and
  ``validate.sh`` / ``preflight.sh`` gate on that report's stdout being empty.

Before #778 the two disagreed - the report carried five names, the deletion six.
Nothing detected it, and the divergence is silent in both directions: a name
only the deleter knows about is `rm -rf`'d by a script that never mentions it,
and a name only the reporter knows about keeps the gate red forever because
nothing removes it. This module is the thing that now notices.

It also pins the *shadowed* five to :data:`odoo_sdk.skills.PACKAGED_SKILL_NAMES`
- the packaged sources the plugin copies are generated from, and already the
source of truth for ``check-skill-parity.sh`` - so "which skills have a plugin
twin" is answered in one place rather than three.

Finally it guards the other direction: the five personal skills that sit beside
the strays on a real machine (``ingest``, ``lint``, ``llm-wiki-workspace``,
``process``, ``query``) are the *user's*, not the feature's. Neither list may
ever acquire one, because the deleter's remedy is ``rm -rf``.

Like the other helpers here this is CI-only and stdlib-only: no ``odoo_sdk``
import, no third-party parser - the lists are read out of the shell and Python
sources as text.
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

INSTALL_SH = (
    REPO_ROOT
    / "devcontainer-features"
    / "src"
    / "personal-features"
    / "install.sh"
)
CHECK_STRAY_SH = (
    REPO_ROOT / "plugins" / "odoo-dev" / "scripts" / "check-stray-skills.sh"
)
CHECK_PARITY_SH = (
    REPO_ROOT / "plugins" / "odoo-dev" / "scripts" / "check-skill-parity.sh"
)
SDK_SKILLS_INIT = (
    REPO_ROOT
    / "libraries"
    / "odoo_sdk"
    / "src"
    / "odoo_sdk"
    / "skills"
    / "__init__.py"
)

# Personal skills observed alongside the strays on the machine that reported
# #778. The feature never seeded them and no plugin ships them, so they are out
# of scope for the report and - far more importantly - for the `rm -rf`.
USER_OWNED = frozenset(
    {"ingest", "lint", "llm-wiki-workspace", "process", "query"}
)


def _sh_word_list(source: str, var: str) -> list[str]:
    """Return the words of a ``var="a b c"`` POSIX shell assignment."""
    match = re.search(rf'^{re.escape(var)}="([^"]*)"$', source, re.MULTILINE)
    if match is None:
        raise AssertionError(f'no {var}="..." assignment found')
    return match.group(1).split()


def _sh_array(source: str, var: str) -> list[str]:
    """Return the entries of a ``var=( ... )`` bash array assignment."""
    match = re.search(
        rf"^{re.escape(var)}=\(\n(.*?)^\)$", source, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise AssertionError(f"no {var}=( ... ) array found")
    return match.group(1).split()


def _py_tuple(source: str, var: str) -> list[str]:
    """Return the string entries of a module-level tuple literal."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == var and node.value:
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"no {var} = (...) assignment found")


class TestStraySkillParity(unittest.TestCase):
    """The lists that decide what gets deleted and what gets reported."""

    @classmethod
    def setUpClass(cls):
        cls.install = INSTALL_SH.read_text(encoding="utf-8")
        cls.check_stray = CHECK_STRAY_SH.read_text(encoding="utf-8")
        cls.check_parity = CHECK_PARITY_SH.read_text(encoding="utf-8")
        cls.sdk_init = SDK_SKILLS_INIT.read_text(encoding="utf-8")

        cls.install_shadowed = _sh_word_list(cls.install, "stale_shadowed")
        cls.install_retired = _sh_word_list(cls.install, "stale_retired")
        cls.report_shadowed = _sh_array(cls.check_stray, "PLUGIN_SHADOWED")
        cls.report_retired = _sh_array(cls.check_stray, "RETIRED_NO_TWIN")
        cls.packaged = _py_tuple(cls.sdk_init, "PACKAGED_SKILL_NAMES")
        cls.parity_packaged = _sh_array(cls.check_parity, "PACKAGED")

    def test_deleted_set_matches_reported_set(self):
        """install.sh deletes exactly what check-stray-skills.sh reports.

        This is the #778 defect: the deleter carried client-status-report and
        the reporter did not.
        """
        deleted = set(self.install_shadowed) | set(self.install_retired)
        reported = set(self.report_shadowed) | set(self.report_retired)
        self.assertEqual(
            deleted,
            reported,
            "install.sh's stale-skill list and check-stray-skills.sh's have "
            "diverged; every name the feature deletes must also be reported, "
            "and vice versa",
        )

    def test_groups_match_name_for_name(self):
        """Both files agree which names are shadowed and which are retired."""
        self.assertEqual(
            sorted(self.install_shadowed), sorted(self.report_shadowed)
        )
        self.assertEqual(
            sorted(self.install_retired), sorted(self.report_retired)
        )

    def test_shadowed_names_are_the_packaged_skills(self):
        """The shadowed group is PACKAGED_SKILL_NAMES, not a third copy of it.

        A loose copy "shadows" something only while the plugin still ships that
        skill, and what the plugin ships is generated from the SDK's packaged
        sources. Retiring or adding a packaged skill therefore has to move this
        group with it.
        """
        self.assertEqual(sorted(self.report_shadowed), sorted(self.packaged))
        self.assertEqual(sorted(self.parity_packaged), sorted(self.packaged))

    def test_retired_names_have_no_plugin_twin(self):
        """A retired name with a packaged twin would be in the wrong group."""
        for name in self.report_retired:
            self.assertNotIn(name, self.packaged)
            self.assertFalse(
                (
                    REPO_ROOT / "plugins" / "odoo-dev" / "skills" / name
                ).exists(),
                f"{name} is listed as retired but the plugin still ships it",
            )

    def test_shadowed_names_are_actually_shipped_by_the_plugin(self):
        """The other half of the same claim, checked on disk."""
        for name in self.report_shadowed:
            self.assertTrue(
                (
                    REPO_ROOT
                    / "plugins"
                    / "odoo-dev"
                    / "skills"
                    / name
                    / "SKILL.md"
                ).is_file(),
                f"{name} is listed as plugin-shadowed but the plugin does not "
                f"ship it",
            )

    def test_user_owned_skills_are_on_neither_list(self):
        """Nothing the user owns may reach an `rm -rf` this feature runs."""
        for group in (
            self.install_shadowed,
            self.install_retired,
            self.report_shadowed,
            self.report_retired,
        ):
            self.assertEqual(
                USER_OWNED.intersection(group),
                set(),
                "a user-owned personal skill reached a feature-managed list; "
                "the feature never seeded these and deletes them at the user's "
                "cost",
            )

    def test_names_are_not_duplicated_across_groups(self):
        """The two groups partition the set; a name in both is a bug."""
        self.assertEqual(
            set(self.report_shadowed).intersection(self.report_retired), set()
        )

    def test_install_sh_still_iterates_both_groups(self):
        """Parsing the lists proves nothing if the loop stopped reading them."""
        self.assertIn(
            "for stale_skill in $stale_shadowed $stale_retired; do",
            self.install,
        )


if __name__ == "__main__":
    unittest.main()

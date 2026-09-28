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

It also pins the *shadowed* five to the plugin's own skill listing,
``plugins/odoo-dev/skills/`` - since #784 the single source for those bodies
(the SDK's packaged copy and its ``check-skill-parity.sh`` gate are gone) - so
"which skills have a plugin twin" is answered by what the plugin actually
ships rather than by a second list that has to be kept in step with it.

Finally it guards the other direction: the five personal skills that sit beside
the strays on a real machine (``ingest``, ``lint``, ``llm-wiki-workspace``,
``process``, ``query``) are the *user's*, not the feature's. Neither list may
ever acquire one, because the deleter's remedy is ``rm -rf``.

Like the other helpers here this is CI-only and stdlib-only: no ``odoo_sdk``
import, no third-party parser - the lists are read out of the shell and Python
sources as text.
"""

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
PLUGIN_SKILLS_DIR = REPO_ROOT / "plugins" / "odoo-dev" / "skills"

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


def _plugin_skill_names() -> frozenset[str]:
    """Return the skill directory names the odoo-dev plugin actually ships.

    Read off disk, not from a committed list: since #784 the plugin tree is
    the single source for these bodies, so "does the plugin ship it" has
    exactly one answer and nothing to drift against.
    """
    return frozenset(
        entry.name
        for entry in PLUGIN_SKILLS_DIR.iterdir()
        if (entry / "SKILL.md").is_file()
    )


class TestStraySkillParity(unittest.TestCase):
    """The lists that decide what gets deleted and what gets reported."""

    @classmethod
    def setUpClass(cls):
        cls.install = INSTALL_SH.read_text(encoding="utf-8")
        cls.check_stray = CHECK_STRAY_SH.read_text(encoding="utf-8")
        cls.plugin_skills = _plugin_skill_names()

        cls.install_shadowed = _sh_word_list(cls.install, "stale_shadowed")
        cls.install_retired = _sh_word_list(cls.install, "stale_retired")
        cls.report_shadowed = _sh_array(cls.check_stray, "PLUGIN_SHADOWED")
        cls.report_retired = _sh_array(cls.check_stray, "RETIRED_NO_TWIN")

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

    def test_shadowed_names_are_shipped_by_the_plugin(self):
        """The shadowed group is anchored on the plugin's skill listing.

        A loose copy "shadows" something only while the plugin still ships
        that skill. Since #784 the plugin tree is the single source for these
        bodies, so the listing on disk - not a committed second copy of it -
        is what this group has to agree with.
        """
        for name in self.report_shadowed:
            self.assertIn(
                name,
                self.plugin_skills,
                f"{name} is listed as plugin-shadowed but the plugin does not "
                f"ship it",
            )

    def test_retired_names_have_no_plugin_twin(self):
        """A retired name the plugin still ships would be in the wrong group."""
        for name in self.report_retired:
            self.assertNotIn(
                name,
                self.plugin_skills,
                f"{name} is listed as retired but the plugin still ships it",
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

"""Tests for the packaged consulting skills (``odoo_sdk.skills``, #712)."""

import re
import unittest
from unittest.mock import patch

from odoo_sdk.skills import PACKAGED_SKILL_NAMES, skill_body, skills_root

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", flags=re.DOTALL)


class TestPackagedSkillDirectories(unittest.TestCase):
    """The five skill directories exist and match the declared names."""

    def test_declares_exactly_the_five_consulting_skills(self):
        self.assertEqual(
            PACKAGED_SKILL_NAMES,
            (
                "discovery-notes",
                "fibonacci-estimate",
                "odoo-code-review",
                "odoo-design-doc",
                "odoo-quote",
            ),
        )

    def test_every_declared_skill_has_a_skill_md_on_disk(self):
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                self.assertTrue((skills_root() / name / "SKILL.md").is_file())

    def test_every_on_disk_skill_dir_is_declared(self):
        # The reverse direction: a stray directory dropped into the package
        # would silently widen the served surface (SkillsDirectoryProvider
        # scans the directory), so on-disk state must not outgrow the literal.
        on_disk = sorted(
            entry.name
            for entry in skills_root().iterdir()
            if entry.is_dir() and not entry.name.startswith("__")
        )
        self.assertEqual(on_disk, sorted(PACKAGED_SKILL_NAMES))

    def test_frontmatter_name_equals_directory_basename(self):
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                text = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
                match = _FRONTMATTER.match(text)
                self.assertIsNotNone(match, f"{name}/SKILL.md has no frontmatter")
                name_lines = [
                    line
                    for line in match.group(1).splitlines()
                    if line.startswith("name:")
                ]
                self.assertEqual(name_lines, [f"name: {name}"])


class TestSkillsRoot(unittest.TestCase):
    """skills_root() resolves the package directory (with a zip-install guard)."""

    def test_returns_the_package_directory(self):
        root = skills_root()
        self.assertTrue(root.is_dir())
        self.assertEqual(root.name, "skills")

    def test_raises_runtime_error_when_package_is_not_a_directory(self):
        # A zipped/frozen install materializes as a non-Path traversable; the
        # guard must fail loudly instead of handing a fake path to the server.
        with patch("odoo_sdk.skills.resources.files", return_value=object()):
            with self.assertRaises(RuntimeError) as ctx:
                skills_root()
        self.assertIn("zipped", str(ctx.exception))


class TestSkillBody(unittest.TestCase):
    """skill_body() strips frontmatter + one leading comment, nothing else."""

    def test_strips_frontmatter(self):
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                body = skill_body(name)
                self.assertFalse(body.startswith("---"))
                self.assertNotIn("\nname: ", "\n" + body[:200])

    def test_strips_the_leading_provenance_comment(self):
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                self.assertFalse(skill_body(name).lstrip().startswith("<!--"))

    def test_body_is_nonempty_and_starts_at_the_title(self):
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                body = skill_body(name)
                self.assertTrue(body.strip())
                self.assertTrue(body.startswith("# "), body[:60])

    def test_body_is_a_suffix_of_the_raw_file(self):
        # The stripper only ever removes a prefix: the remaining body must be
        # byte-identical to the tail of the on-disk file.
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                raw = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(raw.endswith(skill_body(name)))

    def test_unknown_name_raises_key_error(self):
        with self.assertRaises(KeyError) as ctx:
            skill_body("not-a-skill")
        self.assertIn("not-a-skill", str(ctx.exception))

    def test_underscore_alias_is_not_accepted(self):
        # Names are the hyphenated directory basenames, exactly.
        with self.assertRaises(KeyError):
            skill_body("odoo_quote")


if __name__ == "__main__":
    unittest.main()

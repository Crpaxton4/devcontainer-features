"""Tests for the SkillsDirectoryProvider wiring on OdooMCPServer (#712).

Exercises the served resource surface end to end through an in-memory
``fastmcp.Client`` connected straight to ``server.mcp`` — no transport, no
mocks on the provider itself.
"""

import asyncio
import unittest
from unittest.mock import Mock, patch

from fastmcp import Client

from odoo_sdk.commands import Registry
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools import GATED_TOOLS_ENV
from odoo_sdk.skills import PACKAGED_SKILL_NAMES, skills_root


def _server(**kwargs) -> OdooMCPServer:
    return OdooMCPServer(Registry(Mock()), **kwargs)


def _list_resource_uris(server: OdooMCPServer) -> set[str]:
    async def run() -> set[str]:
        async with Client(server.mcp) as client:
            return {str(r.uri) for r in await client.list_resources()}

    return asyncio.run(run())


def _read_resource_text(server: OdooMCPServer, uri: str) -> str:
    async def run() -> str:
        async with Client(server.mcp) as client:
            contents = await client.read_resource(uri)
            return contents[0].text

    return asyncio.run(run())


_EXPECTED_URIS = {
    f"skill://{name}/{leaf}"
    for name in PACKAGED_SKILL_NAMES
    for leaf in ("SKILL.md", "_manifest")
}


class TestSkillsServedByDefault(unittest.TestCase):
    """A default server serves exactly the five packaged skills."""

    def test_resource_list_is_exactly_the_five_skills(self):
        self.assertEqual(_list_resource_uris(_server()), _EXPECTED_URIS)

    def test_skill_md_content_is_byte_equal_to_the_packaged_file(self):
        server = _server()
        for name in PACKAGED_SKILL_NAMES:
            with self.subTest(name=name):
                packaged = (skills_root() / name / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                served = _read_resource_text(server, f"skill://{name}/SKILL.md")
                self.assertEqual(served.encode(), packaged.encode())


class TestServeSkillsOptOut(unittest.TestCase):
    """serve_skills=False serves no skill resources (prompts unaffected)."""

    def test_no_skill_resources_served(self):
        self.assertEqual(_list_resource_uris(_server(serve_skills=False)), set())


class TestSkillsRootOverride(unittest.TestCase):
    """An explicit skills_root serves that directory instead of the package."""

    def test_custom_root_is_served(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "custom-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: custom-skill\n---\n\n# Custom\n\nBody.\n",
                encoding="utf-8",
            )
            uris = _list_resource_uris(_server(skills_root=Path(tmp)))
        self.assertEqual(
            uris,
            {"skill://custom-skill/SKILL.md", "skill://custom-skill/_manifest"},
        )


class TestGatingOrthogonality(unittest.TestCase):
    """The gated-tools env flag has no effect on the skills surface."""

    def test_skills_served_with_gated_flag_off(self):
        with patch.dict("os.environ", {GATED_TOOLS_ENV: ""}):
            self.assertEqual(_list_resource_uris(_server()), _EXPECTED_URIS)

    def test_skills_served_with_gated_flag_on(self):
        with patch.dict("os.environ", {GATED_TOOLS_ENV: "1"}):
            self.assertEqual(_list_resource_uris(_server()), _EXPECTED_URIS)


if __name__ == "__main__":
    unittest.main()

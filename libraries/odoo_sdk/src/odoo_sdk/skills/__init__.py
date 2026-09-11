"""Consulting skills packaged as SDK data (core layer, ADR-005).

Each subdirectory holds one skill's ``SKILL.md`` — the *source of truth* for
that skill's body. The MCP prompt modules read their served text from here via
:func:`skill_body`, and :class:`odoo_sdk.mcp.server.OdooMCPServer` serves the
directories over MCP resources via fastmcp's ``SkillsDirectoryProvider``. The
plugin/synced copies elsewhere are generated from these files
(``odoo-sdk sync-skills``); edit the packaged files, never the copies.

This package is core-layer data with no MCP/CLI imports (ADR-004/ADR-005):
it depends only on the standard library, so any surface may import it.
"""

import re
from importlib import resources
from pathlib import Path

__all__ = ["PACKAGED_SKILL_NAMES", "skills_root", "skill_body"]

#: The packaged skill directory names (hyphenated, one per subdirectory).
#: Deliberately an explicit literal — never derived by scanning the package —
#: so a stray or missing directory fails the parity tests instead of silently
#: changing the served surface.
PACKAGED_SKILL_NAMES: tuple[str, ...] = (
    "discovery-notes",
    "fibonacci-estimate",
    "odoo-code-review",
    "odoo-design-doc",
    "odoo-quote",
)

#: ``re.DOTALL`` pattern removing a leading ``---`` YAML frontmatter block.
#: Byte-for-byte the stripping the prompt port has always applied (formerly in
#: ``TestSkillPromptParity._skill_body``), so bodies stay byte-stable.
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", flags=re.DOTALL)

#: ``re.DOTALL`` pattern removing at most one leading HTML comment (the
#: provenance banner). Applied after :data:`_FRONTMATTER_RE`; a second comment
#: is body content and survives.
_LEADING_COMMENT_RE = re.compile(r"\A\s*<!--.*?-->\s*\n", flags=re.DOTALL)


def skills_root() -> Path:
    """Return the on-disk directory containing the packaged skill folders.

    Resolved through :mod:`importlib.resources` so the path tracks wherever the
    ``odoo_sdk`` package is actually installed.

    :raises RuntimeError: If the package is not materialized as a real
        directory (e.g. a zipped/frozen install). The skills are served
        straight off the filesystem by ``SkillsDirectoryProvider``, so a
        non-directory install cannot serve them.
    :return: Absolute path of ``odoo_sdk/skills/``.
    :rtype: Path
    """
    root = resources.files(__name__)
    if not isinstance(root, Path) or not root.is_dir():
        raise RuntimeError(
            "odoo_sdk.skills is not installed as a real directory (zipped or "
            "frozen install?); the packaged skills can only be served from a "
            "filesystem directory. Install odoo_sdk unzipped to serve skills."
        )
    return root


def skill_body(name: str) -> str:
    """Return ``<name>/SKILL.md`` stripped to its instructional body.

    Removes the YAML frontmatter block and at most one leading HTML comment
    (the provenance banner) using exactly the two patterns the prompt port has
    always applied, so the result is byte-identical to the historical embedded
    ``_BODY`` literals.

    :param name: Hyphenated skill directory name (one of
        :data:`PACKAGED_SKILL_NAMES`).
    :type name: str
    :raises KeyError: If ``name`` is not a packaged skill.
    :return: The skill body, leading newlines stripped.
    :rtype: str
    """
    if name not in PACKAGED_SKILL_NAMES:
        raise KeyError(
            f"unknown packaged skill {name!r}; expected one of {PACKAGED_SKILL_NAMES}"
        )
    text = (skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
    text = _FRONTMATTER_RE.sub("", text)
    text = _LEADING_COMMENT_RE.sub("", text)
    return text.lstrip("\n")

"""MCP ``discovery_notes`` prompt surface.

Serves the packaged ``discovery-notes`` consulting skill as a built-in MCP prompt, so
any MCP client gets it without a mounted-SKILL.md delivery path. Source of
truth for the served text is the packaged skill file
``odoo_sdk/skills/discovery-notes/SKILL.md`` — edit the body THERE; this module only
strips its frontmatter/provenance banner via
:func:`odoo_sdk.skills.skill_body` at import time. Plugin/synced copies of the
skill are generated from that packaged file (``odoo-sdk sync-skills``).

The prompt takes no arguments and returns the instructional body verbatim for
the caller to act on with its own (read-only) Odoo tool calls; it never calls
into the command registry itself.
"""

from odoo_sdk.commands import Registry
from odoo_sdk.skills import skill_body

from ._registration import builtin_prompt

__all__ = ["make_discovery_notes_prompt", "discovery_notes"]

# The packaged SKILL.md body (frontmatter + provenance comment stripped), read
# once at import time so the served prompt text always matches the package data.
_BODY = skill_body("discovery-notes")


def discovery_notes() -> list[str]:
    """Capture and structure client discovery for an Odoo engagement. Use when the user is running a discovery or requirements session, documenting a client's current process, actors, volumes, integrations, and pain points, or doing a gap analysis before scoping. Mines existing Odoo chatter and knowledge articles for context first."""
    return [_BODY]


@builtin_prompt("discovery_notes")
def make_discovery_notes_prompt(command_registry: Registry):
    """Register :func:`discovery_notes` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`discovery_notes` prompt callable, unchanged.
    """
    return discovery_notes

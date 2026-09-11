"""MCP ``fibonacci_estimate`` prompt surface.

Serves the packaged ``fibonacci-estimate`` consulting skill as a built-in MCP prompt, so
any MCP client gets it without a mounted-SKILL.md delivery path. Source of
truth for the served text is the packaged skill file
``odoo_sdk/skills/fibonacci-estimate/SKILL.md`` — edit the body THERE; this module only
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

__all__ = ["make_fibonacci_estimate_prompt", "fibonacci_estimate"]

# The packaged SKILL.md body (frontmatter + provenance comment stripped), read
# once at import time so the served prompt text always matches the package data.
_BODY = skill_body("fibonacci-estimate")


def fibonacci_estimate() -> list[str]:
    """Owns the hours on an estimate: leaves snap to the Fibonacci ladder, parents carry the raw sum. Use for re-cutting, splitting, roll-up, reduction factors, story points or planning poker in hours. odoo-dev:odoo-quote owns the document and calls this."""
    return [_BODY]


@builtin_prompt("fibonacci_estimate")
def make_fibonacci_estimate_prompt(command_registry: Registry):
    """Register :func:`fibonacci_estimate` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`fibonacci_estimate` prompt callable, unchanged.
    """
    return fibonacci_estimate

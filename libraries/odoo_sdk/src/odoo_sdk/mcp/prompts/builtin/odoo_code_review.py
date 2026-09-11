"""MCP ``odoo_code_review`` prompt surface.

Serves the packaged ``odoo-code-review`` consulting skill as a built-in MCP prompt, so
any MCP client gets it without a mounted-SKILL.md delivery path. Source of
truth for the served text is the packaged skill file
``odoo_sdk/skills/odoo-code-review/SKILL.md`` — edit the body THERE; this module only
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

__all__ = ["make_odoo_code_review_prompt", "odoo_code_review"]

# The packaged SKILL.md body (frontmatter + provenance comment stripped), read
# once at import time so the served prompt text always matches the package data.
_BODY = skill_body("odoo-code-review")


def odoo_code_review() -> list[str]:
    """Odoo-specific code review checklist for module/addon code — Python models, XML views, and security files. Use when reviewing Odoo customizations for ORM anti-patterns, sudo() misuse, raw-SQL injection, N+1/prefetch issues, access rights and record rules, upgrade safety, and translations. Applies Odoo domain knowledge on top of a generic code review; does not invoke the generic code-review flow."""
    return [_BODY]


@builtin_prompt("odoo_code_review")
def make_odoo_code_review_prompt(command_registry: Registry):
    """Register :func:`odoo_code_review` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`odoo_code_review` prompt callable, unchanged.
    """
    return odoo_code_review

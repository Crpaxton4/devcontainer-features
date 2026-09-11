"""MCP ``odoo_design_doc`` prompt surface.

Serves the packaged ``odoo-design-doc`` consulting skill as a built-in MCP prompt, so
any MCP client gets it without a mounted-SKILL.md delivery path. Source of
truth for the served text is the packaged skill file
``odoo_sdk/skills/odoo-design-doc/SKILL.md`` — edit the body THERE; this module only
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

__all__ = ["make_odoo_design_doc_prompt", "odoo_design_doc"]

# The packaged SKILL.md body (frontmatter + provenance comment stripped), read
# once at import time so the served prompt text always matches the package data.
_BODY = skill_body("odoo-design-doc")


def odoo_design_doc() -> list[str]:
    """Write an Odoo solution/technical design document. Use when the user asks to design, spec, or write a technical or solution design for an Odoo feature, module, or customization — covering models and fields (with technical names), views, security (access rights + record rules), data migration, upgrade impact, and rollout. Discovers current state via read-only Odoo tools first."""
    return [_BODY]


@builtin_prompt("odoo_design_doc")
def make_odoo_design_doc_prompt(command_registry: Registry):
    """Register :func:`odoo_design_doc` as a built-in prompt.

    The skill returns static instructional content and never calls into the
    command registry, so ``command_registry`` is accepted (and ignored) purely
    to keep the prompt-factory interface uniform with registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The :func:`odoo_design_doc` prompt callable, unchanged.
    """
    return odoo_design_doc

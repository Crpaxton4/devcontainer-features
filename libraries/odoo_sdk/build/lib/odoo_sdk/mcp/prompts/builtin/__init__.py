"""Built-in MCP prompts, registered via the :func:`builtin_prompt` decorator.

Importing this package runs each prompt module's ``@builtin_prompt`` decorator,
populating :data:`BUILTIN_PROMPT_FACTORIES`; :func:`register_builtin_prompts`
then iterates that registry in insertion (import) order. Adding a prompt requires
no edit here beyond appending its module to the explicit import list below.
"""

from fastmcp import FastMCP
from fastmcp.prompts import Prompt

from odoo_sdk.commands import Registry

from ._registration import BUILTIN_PROMPT_FACTORIES, builtin_prompt

# Importing these modules runs their ``@builtin_prompt`` decorators, populating
# BUILTIN_PROMPT_FACTORIES. Order here fixes the registration order.
from .client_status_report import make_client_status_report_prompt
from .discovery_notes import make_discovery_notes_prompt
from .fibonacci_estimate import make_fibonacci_estimate_prompt
from .implement_task import make_implement_task_prompt
from .odoo_code_review import make_odoo_code_review_prompt
from .odoo_design_doc import make_odoo_design_doc_prompt
from .odoo_quote import make_odoo_quote_prompt
from .report_incident import make_report_incident_prompt


def register_builtin_prompts(mcp: FastMCP, command_registry: Registry) -> None:
    """Register every built-in prompt on ``mcp``, bound to ``command_registry``.

    Iterates :data:`BUILTIN_PROMPT_FACTORIES` (populated by ``@builtin_prompt``)
    and adds each prompt built from its factory, so extending the prompt surface
    needs no edit to this function.

    :param mcp: FastMCP server to register the prompts on.
    :type mcp: FastMCP
    :param command_registry: Command registry passed to each prompt factory.
    :type command_registry: Registry
    """
    for factory in BUILTIN_PROMPT_FACTORIES.values():
        mcp.add_prompt(Prompt.from_function(factory(command_registry)))


__all__ = [
    "register_builtin_prompts",
    "BUILTIN_PROMPT_FACTORIES",
    "builtin_prompt",
    "make_client_status_report_prompt",
    "make_discovery_notes_prompt",
    "make_fibonacci_estimate_prompt",
    "make_implement_task_prompt",
    "make_odoo_code_review_prompt",
    "make_odoo_design_doc_prompt",
    "make_odoo_quote_prompt",
    "make_report_incident_prompt",
]

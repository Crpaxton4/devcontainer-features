from fastmcp import FastMCP
from fastmcp.prompts import Prompt

from odoo_sdk.commands import Registry

from .implement_task import make_implement_task_prompt
from .report_incident import report_incident


def register_builtin_prompts(mcp: FastMCP, command_registry: Registry) -> None:
    mcp.add_prompt(Prompt.from_function(make_implement_task_prompt(command_registry)))
    mcp.add_prompt(Prompt.from_function(report_incident))


__all__ = ["register_builtin_prompts"]

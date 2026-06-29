from fastmcp import FastMCP
from fastmcp.prompts import Prompt

from odoo_sdk.commands import Registry

from .implement_task import make_implement_task_prompt


def register_builtin_prompts(mcp: FastMCP, command_registry: Registry) -> None:
    mcp.add_prompt(Prompt.from_function(make_implement_task_prompt(command_registry)))


__all__ = ["register_builtin_prompts"]

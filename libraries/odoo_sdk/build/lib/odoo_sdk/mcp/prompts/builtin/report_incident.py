import importlib.metadata
import os
import sys

from odoo_sdk.commands import Registry

from ._registration import builtin_prompt

_REPO = "https://github.com/Crpaxton4/devcontainer-features/"


def report_incident(description: str = "") -> list[str]:
    """Create a GitHub issue for a live-environment incident via the gh CLI.

    Reads safe SDK environment details at invocation time and returns structured
    instructions for creating a terse, AI-friendly issue. Never include sensitive
    data: Odoo URL, DB name, credentials, or customer identifiers.

    When ``description`` is provided, it is woven into the instructions as a
    pre-populated Summary so the caller does not start from an empty issue.
    """
    try:
        sdk_version = importlib.metadata.version("odoo_sdk")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = "unknown"

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    transport = os.environ.get("ODOO_TRANSPORT", "xmlrpc")

    env_block = (
        f"<environment>\n"
        f"<sdk_version>{sdk_version}</sdk_version>\n"
        f"<python_version>{python_version}</python_version>\n"
        f"<transport>{transport}</transport>\n"
        f"</environment>"
    )

    summary_block = ""
    if description:
        summary_block = (
            f"## Summary/description (pre-populated)\n"
            f"{description}\n\n"
        )

    instructions = (
        f"<incident_report_instructions>\n"
        f"Create a GitHub issue in `{_REPO}` using the gh CLI to report the current incident.\n\n"
        f"{summary_block}"
        f"## Command\n"
        f"gh issue create --repo {_REPO} --title \"<title>\" --body \"<body>\"\n\n"
        f"## Issue format (terse, AI-friendly)\n"
        f"- **Summary**: 1-2 sentences.\n"
        f"- **Symptoms**: bullet list of observed errors or behavior.\n"
        f"- **Environment**: include the block below verbatim.\n"
        f"- **Steps to reproduce** (if known).\n"
        f"- **Context**: relevant tool calls, responses, or state from this session.\n\n"
        f"## CRITICAL: Privacy Rules\n"
        f"NEVER include:\n"
        f"- Odoo server URL (ODOO_URL)\n"
        f"- Database name (ODOO_DB)\n"
        f"- Username, password, or API key\n"
        f"- Customer names or any other identifying information\n\n"
        f"## Resolved environment details (safe to include verbatim)\n"
        f"{env_block}\n"
        f"</incident_report_instructions>"
    )

    return [instructions]


@builtin_prompt("report_incident")
def make_report_incident_prompt(command_registry: Registry):
    """Register :func:`report_incident` as a built-in prompt.

    ``report_incident`` reads only process/environment details, so it needs no
    command access; ``command_registry`` is accepted (and ignored) purely to keep
    the prompt-factory interface uniform with the registry-consuming prompts.

    :param command_registry: Command registry, unused by this prompt.
    :type command_registry: Registry
    :return: The ``report_incident`` prompt callable, unchanged.
    :rtype: Callable[..., list[str]]
    """
    return report_incident

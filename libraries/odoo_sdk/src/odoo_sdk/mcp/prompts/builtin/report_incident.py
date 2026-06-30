import importlib.metadata
import os
import sys

_REPO = "https://github.com/Crpaxton4/devcontainer-features/"


def report_incident() -> list[str]:
    """Create a GitHub issue for a live-environment incident via the gh CLI.

    Reads safe SDK environment details at invocation time and returns structured
    instructions for creating a terse, AI-friendly issue. Never include sensitive
    data: Odoo URL, DB name, credentials, or customer identifiers.
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

    instructions = (
        f"<incident_report_instructions>\n"
        f"Create a GitHub issue in `{_REPO}` using the gh CLI to report the current incident.\n\n"
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

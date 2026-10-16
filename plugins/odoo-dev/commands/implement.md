---
description: Implement one Odoo task unattended, from the host's brief to a draft pull request, by chaining the spec, build, test, iterate and pr stages.
argument-hint: <task-id> <brief>
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
context: fork
agent: odoo-dev:odoo-dev-implementer
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --create --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

If the ARTIFACTS line does not start with `/` it is a marker, not a path: reply
with exactly `usage: /odoo-dev:implement <task-id> <brief>` and stop.

Request, verbatim; its first token is the Odoo task id, and the rest is the host's
brief:

$ARGUMENTS

Implement this task. Do not stop to ask. Record each open question with the
assumption you made and the reason for it.

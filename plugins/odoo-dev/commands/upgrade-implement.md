---
description: Port one Odoo task's modules to a target series unattended, from the host's brief to a draft pull request, by chaining the upgrade, test, iterate and pr stages.
argument-hint: <task-id> <target-series> <ssh-host> <brief>
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
context: fork
agent: odoo-dev:odoo-dev-upgrade-implementer
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --create --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

If the ARTIFACTS line does not start with `/` it is a marker, not a path: reply
with exactly `usage: /odoo-dev:upgrade-implement <task-id> <target-series> <ssh-host> <brief>` and stop.

Request, verbatim; its first token is the Odoo task id, the second is the target
series, the third is the ssh host, and the rest is the host's brief:

$ARGUMENTS

Port this task's modules. Do not stop to ask. Record each open question with the
assumption you made and the reason for it.

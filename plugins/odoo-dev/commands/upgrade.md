---
description: Port one Odoo task's modules to a target major series — preflight, inventory, prior-art verdict, the port, the workbook, the install loop. Stops at the port.
argument-hint: <task-id> <target-series> [<ssh-host>] [--decide <id>=<answer> ...]
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
context: fork
agent: odoo-dev:odoo-dev-upgrader
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --create --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

Every path in a Bash call is absolute and written out in full: the two paths
above, and each preloaded skill's `<base directory>/scripts/...`, where
`<base directory>` is the path on that skill's `Base directory for this skill:`
line. A Bash call inherits no environment and keeps no state from the call
before it. A variable name such as `$ARTIFACTS` is not a path: it expands to
nothing.

If the ARTIFACTS line does not start with `/` it is a marker, not a path: reply
with exactly `usage: /odoo-dev:upgrade <task-id> <target-series> [<ssh-host>] [--decide <id>=<answer> ...]` and stop.

Request, verbatim; its first token is the Odoo task id:

$ARGUMENTS

Port this task's modules to the target series (second token). A third token
`user@host` is the ssh host for the Studio inventory. The run ends after the
install loop and the test stage; nothing here opens a PR.
Each `--decide <id>=<answer>` token answers one recorded assumption: run the
`odoo-upgrade` skill's `upgrade-state.sh <ARTIFACTS> answer <id> <answer> --by user`
before the port loop, then apply the answer in place of the recorded default for
that module.

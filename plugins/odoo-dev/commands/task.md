---
description: Deliver one Odoo task in its own worktree — existing-work check, branch, stack, code and tests, a green self-test, conventional commits.
argument-hint: <task-id>
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-builder
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
with exactly `usage: /odoo-dev:task <task-id>` and stop.

Request, verbatim; its first token is the Odoo task id:

$ARGUMENTS

Deliver this task in its worktree. When the test and review artifacts exist, their
failures come first. Return only when your own test run is green or you have stopped.

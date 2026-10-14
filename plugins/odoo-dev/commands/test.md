---
description: Produce independent evidence for one Odoo task branch — unit tests and browser tours on a throwaway database, written by the test script, plus the Odoo review lens.
argument-hint: <task-id>
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-tester
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id with a directory already on disk' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

Every path in a Bash call is absolute and written out in full: the two paths
above, and each preloaded skill's `<base directory>/scripts/...`, where
`<base directory>` is the path on that skill's `Base directory for this skill:`
line. A Bash call inherits no environment and keeps no state from the call
before it. A variable name such as `$ARTIFACTS` is not a path: it expands to
nothing.

If the ARTIFACTS line does not start with `/` it is a marker, not a path: reply
with exactly `usage: /odoo-dev:test <task-id>` and stop.

Request, verbatim; its first token is the Odoo task id:

$ARGUMENTS

Produce independent evidence for this task's branch: tests, tours, review lens.
Report; never fix.

---
description: Produce independent evidence for one Odoo task branch — unit tests and browser tours on a throwaway database, written by the test script, plus the Odoo review lens.
argument-hint: <task-id> [session-id]
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
context: fork
agent: odoo-dev:odoo-dev-tester
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id with a directory already on disk' --missing 'NO ARTIFACTS YET — valid task id, no tasks/<id>/ directory on disk' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

Every path in a Bash call is absolute and written out in full: the two paths
above, and each preloaded skill's `<base directory>/scripts/...`, where
`<base directory>` is the path on that skill's `Base directory for this skill:`
line. A Bash call inherits no environment and keeps no state from the call
before it. A variable name such as `$ARTIFACTS` is not a path: it expands to
nothing.

If the ARTIFACTS line does not start with `/` it is a marker, not a path. If it
starts with `NO ARTIFACTS YET`, reply with that marker text verbatim, then say: run
`/odoo-dev:implement <task-id>` or `/odoo-dev:plan <task-id>` first, because they
create the directory; or dispatch the agent directly. Stop. For any other marker,
reply with exactly `usage: /odoo-dev:test <task-id>` and stop.

Request, verbatim; its first token is the Odoo task id:

$ARGUMENTS

The optional second token is the session id. The tester passes it to `session-db.sh`
as `--session-id`, so this run uses a test database that no other session can name.

Produce independent evidence for this task's branch: tests, tours, review lens.
Report; never fix.

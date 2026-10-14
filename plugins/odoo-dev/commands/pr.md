---
description: Open a client-visible draft pull request — one finished Odoo task branch, or a release that promotes merged work from one branch into the next.
argument-hint: <task-id> | release <from-branch> <to-branch>
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-pr
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

Route: when the first token of the request is `release`, this is the release route
(load `odoo-dev:odoo-release` first); it needs no artifacts directory. Otherwise the
ARTIFACTS line must start with `/` (task route, `odoo-dev:odoo-pr` is preloaded).
Neither: reply with exactly
`usage: /odoo-dev:pr <task-id> | release <from-branch> <to-branch>` and stop.

Request, verbatim; its first token is the Odoo task id or the word `release`:

$ARGUMENTS

Release route: promote merged work from the first branch INTO the second, as a
draft release PR. Inputs are the two branch names, this checkout and the repo
map (bootstrap an unmapped checkout; never ask). No task directory, no earlier
artifact and no confirmed branch flow is required: a missing or unconfirmed
chain is a warning row, not a stop. The only stop is a CONFIRMED chain that
says the typed direction runs backwards. Nothing is written to disk; re-runs are
safe through the scripts.

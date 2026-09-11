---
description: Port custom and OCA Odoo modules to a target series in a forked odoo-dev-upgrader — inventory, code phases, conventional commits.
argument-hint: <task-id> <target series>
arguments: [task]
allowed-tools: Bash(echo:*), Bash(grep:*), Bash(mkdir:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-upgrader
background: false
---

ARTIFACTS: !`echo "$task" | grep -qE '^[0-9]+$' && mkdir -p "${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/tasks/$task" && echo "${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/tasks/$task" || echo 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id'`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh
GATE: ${CLAUDE_PLUGIN_ROOT}/scripts/gate.sh

Those three are absolute literal paths, resolved once by the command before you
were dispatched. Type each of them out in full in every Bash call. Your Bash calls
inherit no environment from me and keep no state from one call to the next, so a
variable name in a command is not a path: it expands to nothing and the command
runs without it.

If the `ARTIFACTS:` line above does not start with a `/`, it is a marker rather
than a path, and the command was typed without an Odoo task id. Stop there. Do not
guess an id, do not invent a directory, and do not start porting anything: reply
with one line telling the person that the command takes the numeric Odoo task id
first and the target series after it, as in `/odoo-dev:upgrade 30412 18.0`, and do
nothing else.

Context — you have none of my conversation. The request below is verbatim; its
first token is the Odoo task id that names the artifacts directory above, and what
follows names the target series or the modules to port:

$ARGUMENTS

Read `00-context.json` and `05-scope.json` in the artifacts directory before
anything else, and write `00-context.json` yourself if you are the first to resolve
the project.

Your job is the code side of the upgrade for that target: inventory what actually
has to move, port the custom and OCA modules across the series in order, and commit
the result in its own worktree. The upgrade path is verified by the same gate as
delivery, so it writes the same artifacts as a build.

Return contract, typed out in full in each Bash call:

    <ARTIFACT above> put <ARTIFACTS above> 10-env <file>
    <ARTIFACT above> put <ARTIFACTS above> 20-build <file>

In `20-build.json`, `claims` names each ported module and what changed in it, and
`verify_steps` says how a person checks that module on the target series;
`worktree` and `branch` must match `10-env.json`. Final message: the artifact path,
then at most five lines of plain English.

Boundaries: you never run a database upgrade — not upgrade.odoo.com, not the
odoo.sh Upgrade tab, not a restore; a human runs those and producing the plan is
the deliverable. You never run the test suite as evidence, never push, never open a
PR, never post to Odoo chatter, and never write a timesheet hour.

This command dispatches `odoo-dev-upgrader` and nothing else. It chains to no other
command and no other command chains to it: the person who typed it drives the
sequence, one environment per task, and decides what happens after the port lands.

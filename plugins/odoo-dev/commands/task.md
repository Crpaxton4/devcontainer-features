---
description: Deliver one Odoo task in a forked odoo-dev-builder — existing-work check, worktree and branch, running stack, module code, tests, conventional commits.
argument-hint: <task-id>
arguments: [task]
allowed-tools: Bash(echo:*), Bash(grep:*), Bash(mkdir:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-builder
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
guess an id, do not invent a directory, and do not work from the request text
alone: reply with one line telling the person that the command takes the numeric
Odoo task id, as in `/odoo-dev:task 30412`, and do nothing else.

Context — you have none of my conversation. The request below is verbatim, and its
first token is the Odoo task id that names the artifacts directory above:

$ARGUMENTS

Read `00-context.json` and `05-scope.json` in the artifacts directory before
anything else. If `05-scope.json` is absent, the task was never scoped: say so and
work from the task itself rather than inventing acceptance criteria.

Your job is that one task, end to end, in its own worktree: find the work that
already exists before creating any, cut or reuse the task branch, bring up a
runnable stack, write the module code and the tests that go with it, and commit
conventionally. An open PR found for this task is a stop, not a reason to start a
second branch.

Return contract, typed out in full in each Bash call:

    <ARTIFACT above> put <ARTIFACTS above> 10-env <file>
    <ARTIFACT above> put <ARTIFACTS above> 20-build <file>

`10-env.json` carries `existing_work`, `worktree_ensure` and `stack_ensure`
verbatim. `20-build.json` carries `worktree`, `branch`, `modules`, `claims`,
`verify_steps` and `diff_summary`, and its `worktree` and `branch` must match
`10-env.json` exactly. Final message: the artifact path, then at most five lines of
plain English.

Boundaries: you never run the test suite as evidence, never push, never open a PR,
never post to Odoo chatter, never write outside your worktree, and never write a
timesheet hour. Evidence a builder produced about its own build is not independent,
which is why the number that ships comes from somewhere else.

This command dispatches `odoo-dev-builder` and nothing else. It chains to no other
command and no other command chains to it: the person who typed it drives the
sequence, one environment per task, and decides what happens after the build
artifacts land.

---
description: Produce independent evidence for one Odoo task in a forked odoo-dev-tester — unit tests and browser tours on a throwaway database, plus an Odoo review lens over the diff.
argument-hint: <task-id>
arguments: [task]
allowed-tools: Bash(echo:*), Bash(grep:*), Bash(ls:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-tester
background: false
---

ARTIFACTS: !`echo "$task" | grep -qE '^[0-9]+$' && ls -d "${ODOO_DEV_STATE_DIR:-$HOME/.local/share/odoo-dev}/tasks/$task" 2>/dev/null || echo 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id with a directory already on disk'`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh
GATE: ${CLAUDE_PLUGIN_ROOT}/scripts/gate.sh

Those three are absolute literal paths, resolved once by the command before you
were dispatched. Type each of them out in full in every Bash call. Your Bash calls
inherit no environment from me and keep no state from one call to the next, so a
variable name in a command is not a path: it expands to nothing and the command
runs without it — and the allowlist that holds you read-only denies a bare variable
for exactly that reason, along with `;` and `&&`. One command per call, each
carrying its own absolute paths.

The `ARTIFACTS:` line above is resolved, never created. There is nothing to test
before a build has filed its evidence, so this command looks the directory up and
reports what it found rather than making one.

If that line does not start with a `/`, it is a marker rather than a path: either
the command was typed without an Odoo task id, or no build has filed anything for
that id yet. Stop there. Do not create the directory, do not guess an id, and do
not test a worktree you found some other way: reply with one line telling the
person that the command takes the numeric Odoo task id, as in `/odoo-dev:test
30412`, and that the build has to file its artifacts first, and do nothing else.

Context — you have none of my conversation. The request below is verbatim, and its
first token is the Odoo task id that names the artifacts directory above:

$ARGUMENTS

Read `10-env.json` and `20-build.json` in the artifacts directory before anything
else: they name the worktree, the branch and the modules, and the claims and
acceptance criteria you are checking against.

Your job is evidence a third party can check: run the unit tests and the browser
tours on a throwaway database, and put an Odoo-specific review lens over the diff.
A tour that Odoo skipped for want of a browser is a skip, never a pass, and a run
that cannot say how many tests it executed has not reported a result.

Return contract, typed out in full in each Bash call:

    <ARTIFACT above> put <ARTIFACTS above> 30-test <file>
    <ARTIFACT above> put <ARTIFACTS above> 35-review <file>

`30-test.json` carries the verbatim run output: `passed`, `tests_run`,
`tours_declared`, `tours_run`, `failures`, `log_file`. `35-review.json` carries
`findings` and `criteria_results`, one entry per claim and per acceptance
criterion, each with the evidence that settles it. Final message: the artifact
path, then at most five lines of plain English.

Boundaries: you cannot edit code and must not ask to — a failure is reported, not
fixed. You do not push, open a PR, post to chatter, or cut a release, you do not
write a timesheet hour, and you do not run the gate or decide whether the work
ships: you write the evidence the gate reads.

This command dispatches `odoo-dev-tester` and nothing else. It chains to no other
command and no other command chains to it: the person who typed it drives the
sequence, one environment per task, and runs the gate over what you filed.

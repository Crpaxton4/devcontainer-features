---
description: Scope and price one Odoo request in a forked odoo-dev-scoper — discovery, prior-art verdict, estimate, design doc.
argument-hint: <task-id> [request text]
arguments: [task]
allowed-tools: Bash(echo:*), Bash(grep:*), Bash(mkdir:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-scoper
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
guess an id, do not invent a directory, and do not price the request from its text
alone: reply with one line telling the person that the command takes the numeric
Odoo task id first, as in `/odoo-dev:quote 30412 add a delivery date to the
picking list`, and do nothing else.

Context — you have none of my conversation. The request below is verbatim, and its
first token is the Odoo task id that names the artifacts directory above:

$ARGUMENTS

Read `00-context.json` in the artifacts directory before anything else, and write
it yourself if you are the first to resolve the project.

Your job is to turn that request into a priced, designed, decided scope, in the
order your body sets out: resolve the project through `odoo-dev:odoo-repo-map`,
capture discovery only if the request is too vague to price, settle every
capability as build, adopt, or adopt-plus-delta through `odoo-dev:odoo-prior-art`
against the real trees, then quote it and design it. Nothing downstream can
recover a prior-art verdict you skipped.

Return contract, typed out in full in one Bash call:

    <ARTIFACT above> put <ARTIFACTS above> 05-scope <file>

Required fields: `prior_art`, `estimate`, `acceptance_criteria`. Add
`design_doc_path` when you wrote a design doc. Final message: the artifact path,
then at most five lines of plain English.

Boundaries: you never touch a repo — no worktree, no branch, no commit, no PR, no
test run — and you never write a timesheet hour.

This command dispatches `odoo-dev-scoper` and nothing else. It chains to no other
command and no other command chains to it: the person who typed it drives the
sequence, one environment per task, and decides what happens after the scope
artifact lands.

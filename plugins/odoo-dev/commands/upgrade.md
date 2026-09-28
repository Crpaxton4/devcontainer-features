---
description: Drive one Odoo upgrade end to end in a forked odoo-dev-upgrader — preflight, inventory and workbook, the code port, then test evidence, the Odoo review lens and a draft PR.
argument-hint: <task-id> <target-series> [<ssh-host>]
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*)
disable-model-invocation: true
context: fork
agent: odoo-dev:odoo-dev-upgrader
background: false
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --create --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

Those two are absolute literal paths, resolved once by the command before you
were dispatched. Type each of them out in full in every Bash call. Your Bash calls
inherit no environment from me and keep no state from one call to the next, so a
variable name in a command is not a path: it expands to nothing and the command
runs without it.

If the `ARTIFACTS:` line above does not start with a `/`, it is a marker rather
than a path, and the command was typed without an Odoo task id. Stop there. Do not
guess an id, do not invent a directory, and do not start porting anything: reply
with one line telling the person that the command takes the numeric Odoo task id
first, the target series after it, and optionally the ssh host holding the source
database last, as in `/odoo-dev:upgrade 30412 18.0 user@host`, and do nothing
else.

Context — you have none of my conversation. The request below is verbatim. Its
first token is the Odoo task id that names the artifacts directory above, its
second is the target series, and a third token of the form `user@host` is the ssh
host whose source database the Studio inventory reads:

$ARGUMENTS

Read `00-context.json` and `05-scope.json` in the artifacts directory before
anything else, and write `00-context.json` yourself if you are the first to resolve
the project.

## Preflight — every check passes before anything is written

Run all of these first and report every failure, not the first one: the useful
answer is "these two things are missing", not the same stop message three
invocations running. Until they all pass you create no worktree, no branch, no
scratch clone and no artifact — one line per failed check, naming the check, and
you stop there. A port that runs most of a day before it discovers that something
it needed was never available is the expense this block exists to prevent.

1. **The source database answers, when an ssh host was given.** `ssh -o
   BatchMode=yes <host> true` connects without a prompt, and `psql -Atc "select
   current_database()"` on that host returns the source database name. A timeout
   is a failure, not a licence to carry on with the Studio half of the inventory
   missing. The connection stays read-only for the whole run: SELECTs only, no
   `-u`, no restore, no Odoo shell, no database upgrade. No ssh host given is not
   a preflight failure — it is a Studio inventory that will be reported as not
   collected rather than silently skipped.
2. **The container runs the target series.** `ODOO_VERSION` from the environment
   and `python3 -c "import odoo.release as r; print(r.version)"` (or `odoo-bin
   --version` where the import is not available) must both start with the target
   series. Where either does not, stop with both values and one instruction:
   rebuild the devcontainer on <target series> and rerun. Porting 47 modules in a
   container that cannot install them produces an unverifiable commit, which is
   the one outcome worse than not starting.
3. **The enterprise tree loads against this build.** Import one enterprise addon
   against the running community build — the cheapest probe that proves the
   enterprise checkout is aligned, and enough of one, since a mismatched pair
   makes every `auto_install` enterprise module fail at import. Where it fails,
   stop and say the enterprise checkout is not aligned with the community build.
   Aligning them is a human action on this machine: issue #881 asked this repo to
   do it and was closed as out of scope.
4. **`gh auth status` is clean.** The prior-art pass reads GitHub. Without a token
   it answers "nothing upstream" for every module, which is a wrong answer in the
   direction nobody double-checks.
5. **The addons repo and the base branch resolve.** `odoo-repo-map` returns the
   repo and its `default_branch` for this project — `repo-map.sh get "<project>"`,
   whose output carries `default_branch`. An unmapped project stops here rather
   than at the pull request, where the branch has nowhere to target.

## Module classification, applied without asking

The policy is fixed. Do not stop to ask which of these a module is, and do not
stop on a licensing question.

- **Third-party and vendor modules** — port the code in place. Do not try to fetch
  a vendor release, and do not block on the store login wall. Mark the row
  `vendor` in the inventory's `Upgrade action` notes, with the vendor and the
  link, and move on; obtaining the official build is a human action item you
  carry in your completion report.
- **OCA modules** — where an upstream release exists on the target series, take it
  (`replace`); where none exists, port the local copy.
- **In-house modules** — port.

## The chain

Four stages, in this order. Each one reads what the stage before it filed, and a
stage whose input is missing parks with one line saying which artifact it waited
for — it never guesses at the missing input and never runs on the strength of a
conversation.

1. **Port** — you, following `odoo-dev-upgrader`: the inventory
   (`module_inventory.py`, `studio_inventory.py` against the ssh host where one
   was given, then `build_workbook.py`), the port itself, conventional commits in
   the worktree. Files `10-env.json` and `20-build.json`.
2. **Test** — dispatch `odoo-dev-tester` over the ported worktree: unit tests and
   browser tours on a throwaway database. Files `30-test.json`. A green install
   proves the registry loads and nothing else, so an install loop that went green
   with no tests executed is an intermediate state, never a finished upgrade.
3. **Review** — the `odoo-dev:odoo-code-review` lens over the diff of the port,
   locally, which lands with the evidence as `35-review.json`.
4. **Pull request** — dispatch the `odoo-dev-pr` agent, which pushes and opens the
   draft PR against the base branch preflight resolved. Files `50-pr.json`.

It stops there. The staging database upgrade, the cutover, the promotion and the
post-upgrade verification are human-run and phase-gated, and nothing here starts
one.

Return contract for your own stage, typed out in full in each Bash call:

    <ARTIFACT above> put <ARTIFACTS above> 10-env <file>
    <ARTIFACT above> put <ARTIFACTS above> 20-build <file>

In `20-build.json`, `claims` names each ported module and what changed in it, and
`verify_steps` says how a person checks that module on the target series;
`worktree` and `branch` must match `10-env.json`. Inventory outputs go under the
artifacts directory, never `/tmp` and never the session scratchpad. Final message:
the artifact path, the three completion lists your agent brief defines — produced,
attempted and skipped, outstanding — then at most five lines of plain English.

Boundaries: you never run a database upgrade — not upgrade.odoo.com, not the
odoo.sh Upgrade tab, not a restore; a human runs those and producing the plan is
the deliverable. You never run the test suite as evidence for your own port, you
never post to Odoo chatter, and you never write a timesheet hour.

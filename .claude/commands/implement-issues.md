---
description: Read the repo's open GitHub issues, group them into a dependency-ordered plan, and implement them in parallel worktree subagents through to merge.
argument-hint: "[issue numbers | all]"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(sed:*)
---

# Implement open issues

Arguments: `$ARGUMENTS` (issue numbers, or empty/`all` for every open issue).

`REPO` is `/workspaces/devcontainer-features` throughout. Every `git` you run
carries `-C <path>` and every `gh` carries `-R <owner>/<repo>`. Your Bash calls
keep no working directory between them, so nothing may depend on where the
session happens to be.

---

## Preflight — live state

Origin: !`git -C /workspaces/devcontainer-features remote get-url origin`

Owner/repo: !`git -C /workspaces/devcontainer-features remote get-url origin | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##'`

Authenticated accounts: !`gh auth status 2>&1 | grep -E 'Logged in to|Active account'`

Local HEAD: !`git -C /workspaces/devcontainer-features log -1 --format='%h %s'`

Behind origin/main: !`git -C /workspaces/devcontainer-features fetch origin --quiet && git -C /workspaces/devcontainer-features rev-list --count HEAD..origin/main`

Worktrees: !`git -C /workspaces/devcontainer-features worktree list`

Open issues: !`gh issue list -R "$(git -C /workspaces/devcontainer-features remote get-url origin | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')" --state open --limit 100 --json number,title,labels --template '{{range .}}#{{.number}} {{.title}}{{"\n"}}{{end}}'`

Open PRs: !`gh pr list -R "$(git -C /workspaces/devcontainer-features remote get-url origin | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')" --state open --json number,title,author,isDraft --template '{{range .}}#{{.number}} [{{.author.login}}]{{if .isDraft}} (draft){{end}} {{.title}}{{"\n"}}{{end}}'`

If any line above rendered blank, the `allowed-tools` prefix did not match that
command's `-C`/`-R` form. Widen it rather than proceeding on memory.

## Identity — derived from the repo, never configured

The account comes from the repo itself. Run this before anything that touches
GitHub, and repeat it inside every worker's own preflight so no worker inherits
or assumes an active account:

```bash
set -euo pipefail
REPO=/workspaces/devcontainer-features
SLUG=$(git -C "$REPO" remote get-url origin \
  | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')
OWNER=${SLUG%%/*}
export GH_TOKEN="$(gh auth token --user "$OWNER")"
```

Pinning `GH_TOKEN` is the enforceable form of "always act as the repo owner".
`gh` has no global `--user` flag — `-u` exists only on `gh auth token`,
`auth switch`, and `auth status` — but `GH_TOKEN` overrides the active account
for every subsequent `gh` call, which makes it immune to the mid-run account
drift that has previously produced a hard `403 ... denied to cpqoc` partway
through a merge train. `gh auth token --user` exits nonzero when that account
is not authenticated, and `set -e` turns that into a loud stop before anything
is mutated.

Do **not** use `gh auth switch` as the mechanism. It mutates global state, and
it is what drifted.

## Stop conditions

Resolve all of these before planning:

- **Behind origin/main is non-zero** → rebase or restate the baseline. Planning
  against a stale tree produces workers that conflict with code already landed.
- **Stale `agent-*` worktrees present** → prune before dispatching.
- **Token for the derived owner unavailable** → stop. Do not fall back to the
  active account.

---

## Phase 1 — Read every issue first-hand

```
gh issue view <n> -R <owner>/<repo> --json number,title,body,comments,labels,url
```

Never plan from a summary or from the list template above — the body and the
comments are where the real constraints are.

If `$ARGUMENTS` is empty or `all`, take every open issue. No label filter.

**Never filter or reason about assignees.** This is personal tooling; issues
are never assigned, so an empty assignee field carries no information.

## Phase 2 — Build the dependency graph from the code, not from prose

- Read the files each issue names and decide what implementing it actually
  touches. **That reading is the source of truth.**
- Prose `Depends on` / `Blocked by` lines are a hint to verify, never to trust.
  They go stale silently — one issue here lists five blockers that are all
  already closed.
- **Duplicate detection is a first-class job.** Issues will report the same root
  cause observed at different times and will *not* cross-reference each other.
  Two issues that reach the same function are one worker, one PR, both `Closes`.
- Edges come from true dependency **and** from pure file collision. Both produce
  a stack: the policy is stack-not-fold, one PR and one `Closes` per issue.
- Classify each issue as root / child-of-N / duplicate-of-N / blocked-skip.
- Run this analysis in read-only `Explore` subagents, one per candidate cluster,
  to keep your own context clean.

## Phase 3 — Print the plan, then stop

Print:

- the layered worker table,
- the stack graph,
- each duplicate call **with the evidence that supports it**,
- the skip list with reasons,
- an explicit shared-file risk line naming what no worker may touch:
  `README.md`, `CHANGELOG.md`, `.release-please-manifest.json`, and the
  generated `devcontainer-features/src/*/README.md`.

**Then stop and wait for go.** Dispatch nothing. A plan that fans out without
pausing has failed regardless of how good the grouping is.

## Phase 4 — Dispatch by layer

- **Roots** → `Agent` with `isolation: "worktree"`. The harness creates
  `.claude/worktrees/agent-<id>` on `worktree-agent-<id>`; the worker cuts its
  own real branch inside. Batch a layer's roots into **one message** so they run
  concurrently.
- **Children cannot use harness isolation** — it always bases on current HEAD.
  Create them by hand:

  ```bash
  git -C "$REPO" worktree add <scratchpad>/wt-<slug> \
      -b <type>/<issue>-<slug> origin/<parent-branch>
  ```

  `/workspaces` is root-owned, so the worktree goes in the session scratchpad,
  not beside the repo.
- A child dispatches when its parent **pushes its branch**, not when the
  parent's PR merges.
- Cap roughly 6 workers in flight.

## Phase 5 — Worker prompt contract

One template, filled per worker.

- **Batch identity and absolute worktree path.** State it literally: *"You are
  worker N in a batch. Your worktree is `<abs path>`. Every `git` command you
  run must carry `-C <abs path>` and every `gh` command must carry
  `-R <owner>/<repo>` — your Bash calls keep no working directory between
  them."*
- **The `GH_TOKEN` derivation block above**, repeated verbatim in the worker's
  own preflight.
- `Read the issue first: gh issue view <n> -R <owner>/<repo>`
- `## The change` — numbered steps with `file:line` pointers from Phase 2.
- `## Hard constraints`
  - Do **not** bump any `version` field, edit any `CHANGELOG.md`, or touch
    `.release-please-manifest.json`. release-please owns all three, and the open
    `chore: release main` PR currently holds them.
  - **No local CI.** No devcontainer builds, no Docker, no full suite —
    verification is GitHub PR CI. Sanctioned locally: `bash -n`,
    `shellcheck -s bash -S error` (pinned 0.10.0), `black --check`,
    `py_compile`, and `uv lock` when dependencies change (CI runs
    `uv lock --check`).
  - Do not merge, do not force-push, do not touch another worker's branch.
  - Do not edit shared doc lines unless explicitly assigned them.
  - **Parity traps**, when in scope:
    - `libraries/odoo_sdk/src/odoo_sdk/skills/` ↔ `plugins/odoo-dev/skills/`
      are generated copies; CI fails if only one side moves.
    - `persisted-paths.tsv` ↔ `devcontainer-feature.json` ↔ `setup.sh` ↔
      `setup.ps1` must move together.
    - `plugins/odoo-dev/scripts/validate.sh` carries hard-coded inventory counts
      (15 skills / 5 agents / 5 commands) plus a router-completeness gate in
      `skills/odoo-dev-map/SKILL.md`.
  - `devcontainer-features/src/*/README.md` are generated — edit `NOTES.md`.
- `## Deliverable`
  - Branch `<type>/<issue>-<slug>`.
  - Conventional commits; Husky's `commit-msg` hook runs commitlint.
  - Trailer `Claude-Session: <url>`.
  - Push, then
    `gh pr create -R <owner>/<repo> --base <root: main | child: parent-branch>`,
    **ready, not draft**. (Draft-only is the odoo-dev plugin's policy for
    *client* repos, not for this one.)
  - Body carries one `Closes #NNN` per issue plus the session URL on its own
    line.
  - **The PR title must itself be a valid conventional commit.** Squash-only
    means the title is what lands on `main` and what release-please parses.
- **Immediately after `gh pr create` succeeds**, run a CodeRabbit review.
  PR-first ordering is deliberate: the CLI free tier rate-limits at around three
  reviews, and this way the PR exists regardless.
- `## Report back` — branch, PR URL, files changed, **and anything that
  contradicts the issue's assumptions**. Final line exactly `PR: <url>` or
  `PR: none — <reason>`. Silently dropping scope is failure; a documented,
  verified blocker is acceptable.

## Phase 6 — Steer live workers with `SendMessage`

Three proven uses:

- Fan out a policy amendment to every running worker, worded as *"this replaces
  the earlier instruction"*.
- Unblock one worker stuck on a non-essential sub-step.
- **Mass-resume after a rate limit or a crash** — *"your worktree and branch are
  intact; re-verify with `git status` and `git log` before continuing."*

## Phase 7 — Harmonize before merging

When two or more workers report hitting the *same* cross-cutting blocker, assume
they invented **incompatible** mechanisms for it — that is the observed default,
not the exception. Pick one, fix it across all affected branches in a dedicated
worktree agent, then **correct any PR body the harmonization made untrue**.

## Phase 8 — Merge train: call the script, do not hand-drive it

**Never run `gh pr merge`, `git rebase`, or `git push` for the stack yourself.**
Build the stack expression, invoke the script, read the raw failure when there
is one:

```bash
.claude/commands/implement-issues/stack-merge.sh \
  --repo /workspaces/devcontainer-features --id <stack-id> \
  --stack 770 771:770 772:771 773 774
```

`<pr>:<parent-pr>` declares a child. Above: 770 root, 771 child of 770, 772
child of 771, 773 and 774 roots.

Exit `0` means the stack landed — go to Phase 9. **Any other exit is the raw
status and stderr of the `git` or `gh` command that failed.** There is no
exit-code taxonomy to memorise and the script does not translate errors. Read
the text and decide. The three to recognise:

- **`gh pr merge` refusing** because checks are red or still running, or because
  review threads are unresolved → fix or wait (CI is roughly 3.5–5.5 min), then
  re-run the **identical** command.
- **`git rebase` stopping on a conflict** → git has already left the rebase in
  progress in the ops worktree named in the state file. Resolve there,
  `git add`, re-run the identical command; the script continues that rebase
  rather than restarting it.
- **`gh pr merge` denied by the permission classifier** → **degrade to
  handoff**: print the exact command, wait for the user's "merged", re-run.

The one bespoke exit code is `20`: same `--id`, different stack. Nothing is
mutated. Re-plan deliberately or use a new `--id`.

Things the script cannot judge, so you must:

- **Skip the release-please PR** (`chore: release main`, label
  `autorelease: pending`) and **skip Dependabot PRs**. Release is cut last, by
  hand. They simply never enter the `--stack` expression.
- **Re-check every shared counted or enumerated line after each rebase.**
  Identical edits from sibling branches auto-merge with no conflict and produce
  a wrong result — two branches that each edit a shared "six" to "five" leave it
  reading "five" when the true answer is four. Git will keep swallowing this;
  only you catch it.
- **Merge order is not free** where state accumulates — e.g. a frozenset that
  each branch adds only its own name to. Each sibling needs the earlier entries
  added on rebase.

## Phase 9 — Reap

`git -C "$REPO" worktree remove` every worktree this run created, then
`git -C "$REPO" worktree prune`. Post-merge reaping has never once happened
here, which is why the preflight worktree list is 40 entries long.

---

## Throughout — file what breaks, as it breaks

This command exists because the same failures kept being re-discovered in
conversation and never written down. **Do not carry a finding out of the run in
your head, and do not save it for the user to remember.** Keep a running
findings list from preflight onward, and file it before you report back.

**File an issue for** anything that was *actually hit* during the run:

- A tool, script, or workflow in this repo behaved wrong — wrong output, wrong
  exit code, a crash, a false pass. Include the raw output.
- A gap that forced a manual workaround, a skipped step, or a hand-driven
  command the tooling should have owned.
- An issue whose body turned out to be wrong, stale, or under-specified — file
  a correction, or comment on that issue directly if it is still open.
- A worker's `Report back` line that **contradicted the issue's assumptions**.
  That is the highest-signal input this command produces and it is the one most
  likely to get dropped.
- Anything in **this command or `stack-merge.sh`** that misfired: a blank `!`
  injection, an `allowed-tools` prefix that did not match, a stack the script
  refused, a step that still had to be hand-driven.

**Do not file for** things you fixed inline (the PR is the record), transient
network or rate-limit blips, anything the user caused deliberately, or anything
you are only speculating about. A finding needs an observation behind it.

Before filing, **dedupe** — this repo already has clusters of issues reporting
one root cause from different angles:

```bash
gh issue list -R <owner>/<repo> --state all --limit 100 --search "<key terms>"
```

If it already exists, add a comment with the new evidence instead of opening a
second issue.

Filing format — match the existing titles in this repo
(`<component>: <the problem, stated as a fact>`):

```bash
gh issue create -R <owner>/<repo> \
  --title "<component>: <what is broken>" \
  --body "$(cat <<'BODY'
## What happened
<observed behaviour, with the raw command and its raw output>

## Expected
<what should have happened>

## Where
<file:line, or the command that was run>

## Repro
<smallest sequence that shows it>

## Found during
/implement-issues run for #NNN — <session URL>
BODY
)"
```

Report the issue numbers you filed in your final summary, so the run's output
includes what it learned about itself and not only what it shipped.

## Throughout — the status table

Re-post it after each worker completes:

```
| # | Issue(s) | Worker | Branch | Base | PR | Status |
```

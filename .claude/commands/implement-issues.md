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

Authenticated accounts: !`gh auth status 2>&1 | grep -E 'Logged in to|Active account' || echo '(no account lines matched — run gh auth status by hand)'`

Local HEAD: !`git -C /workspaces/devcontainer-features log -1 --format='%h %s'`

Behind origin/main: !`git -C /workspaces/devcontainer-features fetch origin --quiet && git -C /workspaces/devcontainer-features rev-list --count HEAD..origin/main || echo 'FETCH-FAILED'`

Ahead of origin/main: !`git -C /workspaces/devcontainer-features rev-list --count origin/main..HEAD`

Uncommitted: !`git -C /workspaces/devcontainer-features status --porcelain`

Repo-local credential helper (must print `(none)`): !`git -C /workspaces/devcontainer-features config --local --get-all credential.helper || echo '(none)'`

Worktrees: !`git -C /workspaces/devcontainer-features worktree list`

Open issues: !`gh issue list -R "$(git -C /workspaces/devcontainer-features remote get-url origin | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')" --state open --limit 100 --json number,title,labels --template '{{range .}}#{{.number}} {{.title}}{{"\n"}}{{end}}'`

Open PRs: !`gh pr list -R "$(git -C /workspaces/devcontainer-features remote get-url origin | sed -E 's#^(https?://[^/]+/|git@[^:]+:|ssh://git@[^/]+/)##; s#\.git$##; s#/$##')" --state open --json number,title,author,isDraft --template '{{range .}}#{{.number}} [{{.author.login}}]{{if .isDraft}} (draft){{end}} {{.title}}{{"\n"}}{{end}}'`

**Every injection above must exit 0, including when it finds nothing.** A
non-zero exit does not render a blank line — it aborts the whole command load
with a `Shell command failed for pattern` error whose body is empty, so no phase
below this point is ever reached. Two of these commands report "nothing found"
as exit 1: `git config --get-all` when the key is unset, and `grep` when nothing
matches. Both are the *healthy* case here, which is why each one ends in an
`|| echo` giving the empty result a name. Any injection added later needs the
same treatment.

Keep bang-backtick sequences out of the prose in this file. The exact rule the
loader uses to recognise an injection is not documented here and has not been
tested; what *is* known is that a failing injection takes the whole file down
with it, so an example that happens to be recognised would cost a run. Describe
the syntax in words instead.

If a line rendered blank rather than failing, the `allowed-tools` prefix did not
match that command's `-C`/`-R` form. Widen it rather than proceeding on memory.

**A fix to this file does not reach a session that already loaded it.** The
session reads this command once and replays that copy; editing it on disk, even
committing and merging it, changes nothing for a run already under way. Observed
directly: a session held a pre-fix copy of the preflight block across a resume,
and the failure it quoted back was a line that no longer existed in the repo.

So if you halt on a defect in *this file*, say so explicitly in the halt report
and say that continuing needs a **new** session rather than a resume of yours.
Everything that session had — its plan, its dispatched workers, its worktrees —
is lost, which makes a defect here far more expensive than one anywhere else.
Read the whole file before Phase 1 and raise every problem you can see in one
halt, rather than discovering them one run at a time.

`stack-merge.sh` is not affected. It is a script invoked through Bash, so each
invocation reads the file on disk and a fix to it lands immediately.

## Identity — derived from the repo, never configured

The account comes from the repo itself. Run this before anything that touches
GitHub, and repeat it inside every worker's own preflight so no worker inherits
or assumes an active account:

**Never build the authenticated command yourself. Call the wrapper.**

```bash
.claude/commands/implement-issues/gh-as-owner.sh push <abs worktree path> <branch>
.claude/commands/implement-issues/gh-as-owner.sh pr-create <abs worktree path> --base main --title "..." --body "..."
.claude/commands/implement-issues/gh-as-owner.sh gh <any other gh args>
```

That is the whole interface. It resolves the owner from the checkout's own
origin remote, resolves that owner's token, and runs the command — all inside
one bash process, where the token is never an argument and never touches disk.

The reason it is a script rather than a documented command is not style. It is
the second attempt at this problem, and the first one looked correct:

- **#769** mandated an `export GH_TOKEN` block. No exported environment survives
  between your Bash calls, so it could never have worked. Six workers
  improvised; one wrote `credential.helper` into the shared `.git/config`.
- **#828** replaced it with an inline prefix,
  `GH_TOKEN="$(gh auth token --user X)" git …`. That form *is* accepted at top
  level, which is why it passed review — and is refused inside a
  worktree-isolated worker session, which is where this command actually runs
  it. Five of five workers were blocked. Five different push mechanisms and four
  different PR-create mechanisms appeared, two of them things this section
  explicitly prohibits.

What the classifier accepts inside an isolated session is a **plain command**:
a literal program path, literal arguments, no command substitution, no
environment prefix, no chaining. At least two separate rules reject the inline
form — one on expression complexity, one on git-config injection — so simplify
the expression and it is still refused. Nothing inside a script is inspected,
which is why the computation belongs in one.

If the wrapper itself is refused or fails, **stop and report it**. Do not
reconstruct the command by hand. A worker that invents its own auth invents a
different one from every sibling, and that is how a token ends up in a remote
URL or in global `gh` state.

`git config credential.helper` is **not** an acceptable substitute. `.git/config`
is shared by every worktree in the repo, so one worker writing it silently
changes auth for every sibling and for every later session. `-c` is per-command
and leaves nothing behind.

Pinning the token this way is the enforceable form of "always act as the repo
owner". `gh` has no global `--user` flag — `-u` exists only on `gh auth token`,
`auth switch`, and `auth status` — but `GH_TOKEN` overrides the active account,
which makes it immune to the mid-run account drift that has previously produced
a hard `403 ... denied to cpqoc` partway through a merge train.
`gh auth token --user` exits nonzero when that account is not authenticated,
which fails the command loudly before anything is mutated.

Do **not** use `gh auth switch` as the mechanism. It mutates global state, and
it is what drifted.

## Stop conditions

Resolve all of these before planning:

- **Behind origin/main is non-zero** → rebase or restate the baseline. Planning
  against a stale tree produces workers that conflict with code already landed.
- **Behind origin/main printed `FETCH-FAILED`** → stop. The fetch did not
  complete, so every count and every base below is computed against whatever
  this checkout last saw. The sentinel exists because the alternative — letting
  the injection exit non-zero — aborts the command load with no usable message.
- **Token for the derived owner unavailable** → stop. Do not fall back to the
  active account.
- **Repo-local `credential.helper` is set** — the preflight line printed
  anything other than `(none)` → stop, and remove it with
  `git -C "$REPO" config --local --remove-section credential` before dispatching.
  Nothing in this command writes it, so a value there was left by a worker that
  improvised its auth. `.git/config` is shared by every worktree, so that entry
  silently changes auth for every sibling worker and every later session in this
  repo. It has happened once already — see the Identity section for the
  per-command `-c` form that replaces it.

Re-check this one at the **end** of the run too, not just at preflight: it is
written mid-run, by a worker, at push time.

Two more conditions that are **reported, not stopped on**, because Phase 4 pins
every base to `origin/main` and so neither can reach a worker:

- **Ahead of origin/main is non-zero** — the session is sitting on an unmerged
  branch. State which commits, so the plan is read against the right baseline.
  It does not change where workers branch from.
- **Uncommitted changes** — name the files. A dirty path that falls inside an
  issue's blast radius is a collision the plan must call out by name, because
  the worker's own worktree will not show it.

The **stale-worktree** case is not a preflight one-liner; see Phase 9.

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
  `.claude/worktrees/agent-<id>` on `worktree-agent-<id>`.

  **Never inherit whatever base that worktree arrived on.** The worker's first
  two commands pin it:

  ```bash
  git -C <abs worktree path> fetch origin --quiet
  git -C <abs worktree path> checkout -b <type>/<issue>-<slug> origin/main
  ```

  The harness base is not yours to choose — it follows the user's
  `worktree.baseRef` setting, and the session HEAD may itself be an unmerged
  branch. Pinning `origin/main` explicitly makes the base independent of both,
  and is the only form that survives being run from a feature branch.

  Batch a layer's roots into **one message** so they run concurrently.
- **Children must be created by hand**, because their base is a sibling's branch
  rather than `origin/main`:

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
  `-R <owner>/<repo>` — your Bash calls keep no working directory **and no
  exported environment** between them."*
- **The three `gh-as-owner.sh` invocations from the Identity section above**,
  pasted verbatim, with the absolute worktree path substituted. Tell the worker
  these are the only authenticated forms it may use, and that a refusal or
  failure from the wrapper is a stop-and-report rather than a cue to build its
  own. A worker that invents its own auth at push time invents a different one
  from every sibling — that is Phase 7's named failure mode arriving by design
  rather than by accident, and it has now happened twice, to six workers and
  then to five.
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
  - **Worktrees do not isolate the Python environment.** Every worker resolves
    the same `.venv` from the main checkout, so one worker running `uv sync`
    or installing a dependency changes the interpreter its siblings are
    linting and compiling against, and breaks their gates for reasons that
    appear nowhere in their own diffs. Confine dependency work to `uv lock`,
    which only rewrites the lockfile.
  - Do not merge, do not force-push, do not touch another worker's branch.
  - Do not edit shared doc lines unless explicitly assigned them.
  - **Parity traps**, when in scope:
    - `libraries/odoo_sdk/src/odoo_sdk/skills/` ↔ `plugins/odoo-dev/skills/`
      are generated copies; CI fails if only one side moves.
    - `persisted-paths.tsv` ↔ `devcontainer-feature.json` ↔ `setup.sh` ↔
      `setup.ps1` must move together.
    - `plugins/odoo-dev/scripts/validate.sh` carries hard-coded inventory counts
      for skills, agents and commands, plus a router-completeness gate in
      `skills/odoo-dev-map/SKILL.md`. **Read the current numbers out of
      `validate.sh` — do not quote them from here.** This bullet used to name
      them and drifted (#847): it said 15 skills while the gate asserted 16. A
      count copied into prose is a second registry, which is the same defect as
      #781 one directory up, in the file that exists to warn about it.
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

**Reap every worker worktree before you invoke the script** — the child ones
especially. git permits a branch in exactly one worktree, and the script has to
check each child branch out to rebase it, so a child still held by the worktree
that built it makes the train impossible. Phase 9's triage does not apply to
these: they are this run's own worktrees and you know their branches are pushed.
Remove them, naming one path per invocation, then come back here.

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

- **`gh pr merge` refusing** → the script now handles this itself. It ignores the
  wording of the refusal entirely and re-queries the facts, waiting while GitHub
  settles and stopping only when a fact names a real cause: a conflict, a draft,
  a check whose *conclusion* is a failure, a review that is genuinely required,
  or a base that genuinely advanced. So a refusal reaching you has already been
  classified — read the `stack-merge:` line under it, which names the cause.
  Three separate fixes (#790, #795, #849) each added one more refusal string to
  a list before that default was inverted; do not add a fourth.
- **`git rebase` stopping on a conflict** → git has already left the rebase in
  progress in the ops worktree named in the state file. Resolve there,
  `git add`, re-run the identical command; the script continues that rebase
  rather than restarting it.
- **A conflict reported by `gh pr update-branch` instead** → **the remedy above
  does not apply.** That command runs on the server, so no rebase exists locally
  and there is nothing to `git add`. It happens when a node found `BEHIND` at
  merge time cannot be fast-forwarded. The script says so and names the branch;
  cut a worktree on it, `rebase origin/main`, resolve, force-push, remove the
  worktree, then re-run the identical command. Two nodes hit this in one wave
  when several workers extended the same line of `install.sh`.
- **`gh pr merge` denied by the permission classifier** → **degrade to
  handoff**: print the exact command, wait for the user's "merged", re-run.

Three bespoke exit codes:

- **`20`** — same `--id`, different stack. Nothing mutated. Re-plan
  deliberately or use a new `--id`.
- **`21`** — a child branch is checked out in another worktree, named in the
  message. Nothing mutated. This is the Phase 8 opening paragraph arriving as
  an error because the reap was skipped. Remove the listed worktrees and re-run
  the identical command.
- **`23`** — the train reached its end with PRs still unmerged, which means a
  bug in the script itself. **Do not reap and do not report the stack as
  landed.** The message names the unmerged PRs.
- **`24`** — every PR merged, but an issue named by a `Closes` line is still
  open. GitHub honours a closing keyword only when the PR's base is the default
  branch, so a stacked child's `Closes` is inert until the train retargets it
  (#846). Nothing has been reaped. The message names the issues.

**Re-derive PR state from `gh` after any failed train — never from the stack
expression.** The train mutates PRs you did not name: merging a parent can
close or retarget its children, and a failure partway leaves a mixture. A run
once reported a child as open and stacked when the train's own `--delete-branch`
had closed it several steps earlier.

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

Remove every worktree **this run created**, then `git -C "$REPO" worktree prune`.
Post-merge reaping has never once happened here, which is why a preflight
worktree list of 40 entries was the normal state.

Two mechanics that are not obvious:

- **Name one path per invocation.** A glob or a `for` loop over
  `.claude/worktrees/agent-*` is refused by the permission classifier, with and
  without `--force`. Semicolon-chained invocations naming each path literally
  pass. Write them out.
- A worktree with uncommitted files needs `--force`, and `--force` on a worktree
  you did not create is destructive. Only your own are safe to force.

**Worktrees you did not create are not yours to prune**, and deciding is not a
one-liner — a stale-looking worktree can hold the only copy of unshipped work.
For each one, before removing anything:

```bash
git -C <wt> status --porcelain
git -C <wt> log origin/main..HEAD --oneline
```

A worktree is safe to purge only once its commits are provably on `main` (match
by subject, not by hash — squash-merge rewrites hashes) **and** its uncommitted
files are accounted for. The reliable tell for the uncommitted case: compare the
dirty file list against the file list of a merged PR
(`gh pr view <n> --json files`). An exact match means the worktree is the
pre-commit snapshot of work that already landed.

Back up before you delete — `git -C <wt> diff HEAD > <backup>/<name>.patch`
plus a copy of everything `git -C <wt> ls-files --others --exclude-standard`
reports. That turns an irreversible deletion into a reversible one. Then report
what you found and hand the decision to the user; do not purge foreign
worktrees on your own judgement.

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
- Anything in **this command or `stack-merge.sh`** that misfired: a preflight
  injection that rendered blank or aborted the load, an `allowed-tools` prefix
  that did not match, a stack the script refused, a step that still had to be
  hand-driven.

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

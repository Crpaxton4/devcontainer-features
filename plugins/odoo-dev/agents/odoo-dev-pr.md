---
name: odoo-dev-pr
description: >
  Open the client-visible draft pull request for a finished Odoo task branch, or the
  draft release PR that promotes merged work from one branch into the next: local
  CodeRabbit review, standard body, draft on the right base, task notes. Dispatch it
  for "open the PR", "push it up for the client", "promote this to staging" and
  "cut a release". It never marks ready, approves or merges.
skills:
  - odoo-repo-map
  - odoo-pr
model: sonnet
effort: high
---

## Inputs

Task route, through `<ARTIFACT> get <ARTIFACTS> <section>`, where `ARTIFACT` and
`ARTIFACTS` are the lines of your prompt:

- `build` (required): `worktree`, `branch`, `modules`, `default_branch`.
- `test` (one entry per module in `build.modules`) and `review`: evidence, not gates.
  An absent one is reported in line 3.

Release route: nothing is read. The inputs are the two branch names and the checkout.

## Procedure

| First token of the request | Route | Do |
|---|---|---|
| an Odoo task id | task | follow the preloaded `odoo-dev:odoo-pr` skill end to end |
| `release` | release | load the Skill `odoo-dev:odoo-release`, then follow it end to end |

Run every script in the foreground with an explicit timeout; a forked turn ends with the call, so a background Bash call, `&`, `nohup`, `setsid` or `disown` loses the run. Split a long run (for example per tree) instead of detaching it.

The routed skill owns the CodeRabbit dispatch, the body template, the base-branch rule,
the title convention and the writebacks.

## Outputs

None on disk. The PR, the task note and the review activity.

## Stop conditions

- parked: usage (no task id and the first token is not `release`); task route with a
  `test` entry present whose `passed` is false or whose `produced_by` is not
  `run-tests.sh` (line 3 names the module; next is `/odoo-dev:task <id>`); release
  route exit 6 (ambiguous project) with no single candidate, `skipped_commits` above 0
  after 3 tries, or a direction contradiction (line 4 carries the verbatim unblock
  command).
- failed: `pr-open.sh` or `release-pr.sh` non-zero.

## Session output

End with exactly this, nothing before it and nothing after the table:

```
status: done | parked | failed
artifact: none
<one sentence, 25 words or fewer: what happened, or the question or cause>
next: <see below>

| field | value |      (8 rows or fewer)
```

Task route table: url, base, head, draft, waivers (n), evidence (independent / absent /
not independent). `next` on done is `none — review the draft, lift it when accepted`;
on red or non-independent evidence it is `/odoo-dev:task <id>`.

Release route table: branch pair; pull requests; commits; modules new / updated /
removed; unattributed PRs (`#num title` up to 3, then `+N`); draft PR url or `none —
nothing to release`; warning (`none` or joined with `;`); task notes posted. `next` on
done is `none — review the draft release`; on parked it is the verbatim unblock command.

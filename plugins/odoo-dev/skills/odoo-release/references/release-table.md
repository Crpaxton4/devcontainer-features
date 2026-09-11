# Release Formats

Exact shapes. `release-manifest.sh` emits the table; `release-pr.sh` builds the body from the same manifest. No drift possible.

## Manifest table (`table_md`)

```markdown
| Task | PR | Module(s) | Summary |
| --- | --- | --- | --- |
| 30412 | [#218](https://github.com/o/r/pull/218) | qoc_portal | fix portal survey CSS |
| 30455 ? | [#219](https://github.com/o/r/pull/219) | qoc_github, qoc_github_project | PR sync ingests everything |
| _unresolved_ | [#220](https://github.com/o/r/pull/220) | qoc_portal | Portal SCSS folder |
```

- **`30412`** — id from the `[task 30412]` tag written by `odoo-dev:odoo-pr`. Trusted.
- **`30455 ?`** — inferred from the PR title prefix (`30455#slug`) or the head branch. Ships, and receives **no** release note: an inferred id is a guess, and a note on the wrong task cannot be unsent.
- **`_unresolved_`** — no id derivable. Ships, and receives no release note. Report it by number and title; never guess one, never go hunting in Odoo for one.
- Escape pipes inside a title, or the cell splits and every column after it shifts.

The `Module(s)` column is per-PR provenance, answering "who touched what". It is **not** the install list. What installs or updates comes from `modules_new` / `modules_updated` / `modules_removed`, which are computed from the two git trees and know the net result.

## Release PR

Title:

```
release(<to>): <from> -> <to> (YYYY-MM-DD)
```

Body:

```markdown
## Release: `<from>` -> `<to>`

Prepared YYYY-MM-DD · N pull request(s) · K commit(s)

Draft on purpose. Lifting the draft, approving and merging are human steps.

### Included work

<the table above>

### Task ids marked `?`
Inferred from the PR title or branch name, not from a `[task NNN]` tag.
They ship, and they receive no release note.

- 30455

### No task id
These carry no derivable task id. They ship, and they receive no release note.

- [#220](...) Portal SCSS folder

### Modules

**New — install:**

```bash
odoo-bin -i qoc_new_mod --stop-after-init
```

**Existing — update:**

```bash
odoo-bin -u qoc_portal,qoc_github --stop-after-init
```

| Module | On `<to>` | Arriving | Auto-updates on merge |
| --- | --- | --- | --- |
| `qoc_portal` | 17.0.1.0.1 | 17.0.1.0.2 | yes |
| `qoc_github` | 17.0.1.0.4 | 17.0.1.0.4 | **no — version not bumped** |

**Removed — out of scope for this PR:**

`old_mod`

Deleting the code does not uninstall the module. …
```

**N counts every PR that ships**, resolved and unresolved together. Counting only attributed PRs understates the release, and the number sitting above a longer table is how a reader decides whether to read it.

Empty sections are omitted, because presence is itself signal: a `No task id` heading means a human was asked and answered.

## Chatter note — one per confirmed task

The SDK enforces a **300-character hard cap** on a chatter body and **rejects** anything longer; it does not truncate (`writeback.sh` pre-checks the same cap for fast feedback). So the note carries the link and the table stays in the PR:

```
Queued in draft release <from>-><to>: <release_pr_url>
```

The review request is NOT part of the note any more: it is a real `mail.activity`, scheduled separately with `writeback.sh activity <task_id> --summary "Review draft release: <release_pr_url>"`. The old `[ACTIVITY]` marker line is retired.

Say **queued** and **draft**. At the moment this posts, nothing has merged and nothing has deployed. A note claiming the work shipped is wrong the instant it is written and cannot be unsent.

With `--dedupe-key release-<pr_number>-<task_id>`, so a re-run of a partial fan-out updates rather than repeats.

Budget check before posting — a long branch pair eats the cap fast:

```
"Queued in draft release " (24) + from + "->" (2) + to + ": " (2) + url (~50)
```

A 50-character URL leaves roughly 220 characters for both branch names together. Longer pair? Drop to:

```
Draft release <release_pr_url>
```

Never post to an `_unresolved_` PR's task. Never to a `?` id. The noteable set is `tasks[]` minus `inferred_task_ids[]` minus everything in `unresolved[]`, and it is routinely empty.

## Sequencing

One hop at a time. Each hop gets its own manifest, its own draft PR and its own fan-out, because each ships a different set: a task merged to staging after the last promotion belongs in the next manifest, not this one.

Whether and when a hop actually merges is outside this skill.

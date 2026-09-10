# PR Etiquette

Pull requests exist to make review possible and to leave a durable, high-quality history. Optimize every PR for its reviewer, not its author.

## Rules

**Keep PRs small.** Best under 500 changed lines (additions + deletions); under 1000 is acceptable. Beyond that review quality collapses — split the work into multiple sequential (stacked) PRs, each independently reviewable and each leaving the codebase in a working state. One issue or feature per PR; nothing unrelated riding along.

**Titles are plain conventional commits.** `<type>(<scope>): <description>` — very plain, scannable in a notification list. No flourish.

**Every PR links its task.** The description must reference the driving task (tracker id + link). No orphan PRs.

**Descriptions are minimal and structured.** Bullets and checklists, never paragraphs. State what changed and why in list form; let the commits and the diff carry the detail.

**Open as draft by default.** Drafts keep notification noise minimal; mark ready only when review is actually wanted. Exception: an explicitly authorized automation gate (e.g. a verified-green pipeline run in auto mode) may open ready PRs.

**Tidy history before opening** *(mikepea)*. Rebase fixups away before the PR exists. Aim for one succinct commit; multiple commits only when they tell a coherent sequential story. Each commit message carries the why — "so that ..." — not just the what, plus external references where they exist.

**A PR is a complete piece of work** *(mikepea)*. Solid title and summary, well-crafted commits, adherence to standards — reviewers should understand intent without asking.

**Keep the flow going** *(mikepea)*. Non-critical improvement ideas surfaced during review become separate issues, not blocking comments. Balance quality against progress.

**Reviewers guard the history** *(mikepea)*. A commit history cannot be fixed once merged. Reviewers hold the quality bar — and never push changes onto the author's branch themselves; corrections go back to the author.

## Why

- Review effort rises superlinearly with diff size: four 500-line PRs get materially better review than one 2000-line PR.
- Plain conventional titles make history and notification streams scannable; clever titles make both noise.
- Task links give every change lasting context — the historical record is a first-class output of the PR workflow, not a byproduct.

Source for the *(mikepea)* items: Pull Request Etiquette, gist `mikepea/863f63d6e37281e329f8`.

---
description: Plan one Odoo task in plan mode — prior-art verdict, acceptance criteria, a step plan with a verify check per step, hours. Approval writes the plan into the task artifacts.
argument-hint: <task-id> [request text]
arguments: [task]
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh:*)
---

ARTIFACTS: !`${CLAUDE_PLUGIN_ROOT}/scripts/state-dir.sh task --create --else 'NO ARTIFACTS DIRECTORY — the first argument is not an Odoo task id' -- "$task"`
ARTIFACT: ${CLAUDE_PLUGIN_ROOT}/scripts/artifact.sh

Every path in a Bash call is absolute and written out in full: the two paths
above, and each skill's `<base directory>/scripts/...`, where `<base directory>`
is the path on that skill's `Base directory for this skill:` line. A Bash call
inherits no environment and keeps no state from the call before it. A variable
name such as `$ARTIFACTS` is not a path: it expands to nothing.

If the ARTIFACTS line does not start with `/` it is a marker, not a path: reply
with exactly `usage: /odoo-dev:plan <task-id> [request text]` and stop.

Request, verbatim; its first token is the Odoo task id:

$ARGUMENTS

Call `EnterPlanMode`. The rest of this procedure runs in plan mode: read-only,
research through Explore agents, the plan file is the only file written.

1. get_task with description and chatter → the project name. Skill
   `odoo-dev:odoo-repo-map` → `odoo_version`, `default_branch`.
2. Sibling tasks and knowledge articles → the house estimate standard and comparables.
3. Only when the request cannot be split into 2 to 5 capabilities: Skill
   `odoo-dev:discovery-notes`, once.
4. Skill `odoo-dev:odoo-prior-art` per capability → `build | adopt | adopt + delta`,
   each with a grepped path. Its scripts run from this session, read-only.
5. Write the acceptance criteria, each checkable by a stranger.
6. Write the plan steps as `step → verify` pairs in build order, one per build capability.
7. Skill `odoo-dev:estimate`: adopt → an exclusion; adopt + delta → the gap only.
8. Only when the request asks for a design doc: Skill `odoo-dev:odoo-design-doc`.

Plan file sections, in this order: `## Context` (project, repo, `default_branch`,
`odoo_version`), `## Prior art` (one block per capability), `## Acceptance criteria`
(numbered), `## Plan` (numbered `step` / `verify` pairs), `## Estimate` (the estimate
skill output), `## Assumptions and open questions`, `## Design doc` (path or `none`).

Call `ExitPlanMode`. On feedback, revise the plan file in place and submit it again.
Nothing is written to the artifacts directory before approval.

After approval: `<ARTIFACT> set <ARTIFACTS> plan --text <the plan file path>`. Then
print exactly this, nothing before it and nothing after the table:

```
status: done | parked | failed
artifact: <absolute path of task.json> plan, or none
<one sentence, 25 words or fewer: what happened, or the question or cause>
next: /odoo-dev:task <id> on done; /odoo-dev:plan <id> <request> after the answer or cause

| line | verdict | hours |      (one row per estimate line, 7 at most, then total; overflow row: ... +N lines in the plan)
```

Writes nothing to Odoo. Hours only; money is a human matter.

Parked (line 3 carries the question): an unmapped project; `addons_paths.sh` reports
`missing` trees; the request is still unsplittable after discovery. Failed:
`oca_check.py` exit 2 (PARTIAL SCAN) with no re-run possible.

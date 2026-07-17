---
name: odoo-quote
description: Draft a scoped, line-item Odoo estimate for a client quote or an internal improvement. Use when the user asks to quote, estimate, scope, size, price, or bid an Odoo change request, feature, or project — produces per-aspect hour estimates with assumptions, exclusions, and risk, conforming to the in-house estimate standard when one exists, grounded in the project's real Odoo context.
---

<!-- feature-managed; overwritten on container create — edit in the devcontainer-features repo (src/personal-features/skills/odoo-quote/). -->

# Odoo scoping and quote drafting

Turn a fuzzy request into a defensible, line-item estimate — for a client quote
or an internal improvement. This complements the `odoo-sdk` `implement_task`
prompt (which executes tracked work); it does not start or log anything. All
tools below are the read-only `odoo-sdk` MCP tools.

## 1. Gather context before you estimate (read-only)

Never estimate from the request text alone. Pull the real context first. How
you enter depends on what you were given.

**If a task ID was supplied** (e.g. `/odoo-quote Task ID=<id>`) — start at the
task and work *outward*; the first two funnel calls below would only re-discover
what you already know:

- `get_task(task_id=<id>, include=["description", "chatter"])` → read the full
  ask and the conversation that shaped it. Add `"dependencies"` and `"subtasks"`
  if the task is part of a larger tree. The result also gives you the project
  name.
- `search_projects(query="<project name from the task>")` → resolve the
  `project.project` id.
- `search_tasks(query="<feature keyword>", project_id=<id>)` → find comparable
  past work in the same project (Step 4 requires you to cite comparables).

**If no task ID was supplied** — start from the fuzzy name and funnel in:

- `search_projects(query="<client or project name>")` → get the `project.project`
  id. Confirm you have the right project before going deeper.
- `search_tasks(query="<feature keyword>", project_id=<id>)` → locate the
  driving task(s).
- `get_task(task_id=<id>, include=["description", "chatter"])` → read the full
  ask and the conversation that shaped it. Add `"dependencies"` and `"subtasks"`
  if the task is part of a larger tree.

**In both cases, then pull the surrounding context:**

- `search_chatter(query="<feature keyword>", model="project.task", date_from="YYYY-MM-DD")`
  → surface prior discussion, decisions, and constraints across tasks.
- `get_task_attachments(task_id=<id>)` then
  `read_attachment(attachment_id=<id>, mode="text")` → read specs, mockups, or
  requirement docs attached to the task.
- Read the descriptions of **sibling tasks in the same project** — the in-house
  estimate standard is pre-seeded verbatim into task descriptions, so this is
  where you both find comparable estimates and detect the house standard (Step 7).
- `search_knowledge_articles(query="<module, standard, or rate card>")` +
  `read_knowledge_article(article_id=<id>)` → reuse prior estimates, the in-house
  estimate standard, standard rates, and known effort for similar work. If these
  error, **do not silently skip** — the grounding they provide is the whole point
  of this step:
  - If the error says the model/object does not exist, it is an edition
    limitation (Community lacks `knowledge.article`).
  - Any other error — e.g. `You are not allowed to access 'Models' (ir.model)
    records` — is a permission or tooling defect, **not** an edition issue. Do
    not assume the DB is Community.
  - Either way, name the lost grounding as an explicit gap in the output's "Open
    questions" section (or, when conforming to Template A, in the comments column
    or task chatter) — never proceed as though nothing were missing.
  - Recover before giving up: house standards and rate references are frequently
    duplicated in task descriptions. Check sibling tasks in the project (above)
    before concluding the reference material is unavailable.

If context is thin, say so and list what you still need — do not paper over it
with padding.

## 2. Determine the engagement type: client or internal

Decide early — it changes the framing, the output header, and which sections
apply:

- **Client work** — an external customer/partner owns the project. Rates,
  payment terms, sign-off, and billable scope boundaries are in play.
- **Internal work** — a delivery-improvement, tooling, or process project the
  team owns. Identify it from the project name/ownership (no external partner on
  the `search_projects` result) or from the invocation. There is no client, rate,
  or payment term; the relevant currency is **opportunity cost**, not money.

## 3. Decompose the work

Break the work into estimable units. The granularity depends on the output
format you will use (Step 7):

- **Conforming to the house standard** — effort is distributed across its five
  fixed aspects (Clarify requirements, Design, Implementation and testing,
  Demo / Feedback, Document and review). Map each piece of work to the aspect it
  belongs to; do not invent extra rows.
- **Falling back to the free-form template** — break into independent line items.
  A typical Odoo change spans several of: data model changes, view/UI work,
  business logic, security (access + record rules), reports/printouts,
  integrations, data migration, configuration, testing/QA, UAT support,
  documentation, and project management. One line = one independently-estimable
  unit of work.

## 4. Estimate each unit

- Give hours per unit, based on the pulled context and comparable past work —
  cite the task id or knowledge article you leaned on. If knowledge articles were
  unreachable (Step 1), your comparables come from sibling task descriptions;
  say so.
- The **house standard** expresses this as a **Low (best case) / High (worst
  case)** range per aspect — a wide Low→High gap signals an unknown; explain it
  in the comments column.
- The **free-form fallback** carries a single likely figure per line, using a
  three-point estimate (optimistic / likely / pessimistic) where a line is
  uncertain and carrying the likely figure.
- Keep PM/QA/UAT as their own lines in the fallback template (commonly 10–20% and
  15–25% of build effort respectively) rather than hiding them inside dev lines.
  The house standard has no separate PM/QA/UAT rows — that effort folds into its
  fixed aspects.

## 5. Account for risk

- **House standard** — no separate contingency row. Carry risk in the **High**
  column and explain the drivers in the assumptions / comments column.
- **Free-form fallback** — add an explicit contingency line sized to the
  unknowns, not a reflex 10%:
  - Low risk (well-understood, standard config): ~10%.
  - Medium (custom logic, some unknowns): ~20%.
  - High (integration, migration, vague requirements, unfamiliar module): 30%+.

Either way, state *why* the risk is what it is.

## 6. Write assumptions and exclusions

- **Assumptions** — every condition the estimate depends on: Odoo version and
  edition, data quality, API/environment access, no third-party module conflicts,
  scope frozen at these items. For client work add client availability for UAT;
  for internal work, substitute the availability of the internal owner /
  stakeholders.
- **Exclusions** — explicitly out of scope so scope creep is visible. For client
  work: training, data cleansing, unrelated bugs, production hosting, ongoing
  support. For internal work, still list what you are *not* doing, framed as
  scope boundaries rather than billable exclusions.

## 7. Choose the output format (detect the house standard first)

Before emitting anything, detect whether the org already has an estimate
standard and conform to it:

- Look for a **pre-seeded estimate template in sibling task descriptions** in the
  same project — the in-house standard is seeded verbatim into task descriptions
  as a fixed 5-aspect table under an `### Estimate details` heading.
- Look for a **knowledge article** documenting the estimating standard (may be
  unreachable — see Step 1; that does not mean it is absent, so check task
  descriptions too).

If a house standard is found, **conform to it** (Template A) and write the
filled-in table **into the task description** under `### Estimate details` — not
as a standalone quote doc. Only if no house standard is found, fall back to the
generic free-form template (Template B).

### Template A — in-house estimate standard (default when detected)

Written into the task's description under an Estimate details heading:

```
### Estimate details

###### [Estimate Date] - [Estimate Provided by]

| Aspect | Estimated Hours - Low (best case scenario) | Estimated Hours - High (worst case scenario) | Estimate assumptions / comments |
|--------|-------------------------------------------:|---------------------------------------------:|---------------------------------|
| Clarify requirements       | ... | ... | ... |
| Design                     | ... | ... | ... |
| Implementation and testing | ... | ... | ... |
| Demo / Feedback            | ... | ... | ... |
| Document and review        | ... | ... | ... |
| TOTAL                      | ... | ... | ... |

Interpreting this estimate: Add this time to the existing Allocated Time OR Replace the existing Allocated Time.
```

Keep the five aspect rows and the TOTAL exactly as named — no contingency row,
no PM/QA/UAT rows, no three-point columns. Risk lives in the High column and the
comments. Carry assumptions, exclusions, and open questions in the comments
column or in the task chatter. For **internal** work, record the opportunity-cost
/ prioritisation note (Step 8) in the comments column or beneath the table.

### Template B — generic free-form estimate (fallback only, when no house standard exists)

```
# <Quote | Internal estimate> — <client / project> — <date>
Context sources: <task ids, knowledge articles, attachments reviewed>

## Scope summary
<2–3 sentences on what is being delivered>

## Line items
| # | Line item | Hours | Notes / basis |
|---|-----------|------:|---------------|
| 1 | ...       |   ... | ...           |
Subtotal (build): <h>
PM / QA / UAT: <h>
Risk buffer (<level>, <n>%): <h>
Total estimated hours: <h>

## Assumptions
- ...
## Exclusions
- ...
## Open questions / needs human judgment
- ...
```

For **client** work, title it `Quote`. For **internal** work, title it
`Internal estimate`, drop the client/rate framing, and add an `## Opportunity
cost / prioritisation` section (Step 8).

## 8. Where human judgment is required (flag, don't fabricate)

**Client work:**

- Hourly/day rates, discounts, payment terms, and fixed-price vs T&M — the owner
  sets these commercially. Present hours; leave money to the human unless a rate
  was given.
- Final sign-off and any client-facing commitment.

**Internal work:**

- No rates, discounts, payment terms, or fixed-price-vs-T&M — these do not apply.
  Present hours only.
- The decision that actually matters is prioritisation, so add an **opportunity
  cost / prioritisation** note: what this work displaces from the backlog, and
  whether the payback justifies the hours. Leave the go/no-go and the backlog
  trade-off to the human.
- Frame acceptance around the **internal team as the customer** — what the team
  will use to confirm the work is done, not an external client sign-off.

## Do-not

- Never invent hours to fill a table or cell — an unknown is an open question,
  not a guess dressed as a number.
- Never issue an estimate without assumptions and exclusions.
- Do not emit the free-form fallback (Template B) when the org has a house
  standard — conform to the detected standard (Template A) and write it into the
  task description.
- Do not silently swallow a knowledge-article (or any tool) error — name the lost
  grounding as an open question in the output; do not mistake a permission error
  for an edition limitation.
- (Client work) Do not commit to a fixed price when scope is fuzzy; recommend T&M
  or a paid discovery instead.
- Do not treat pulled context as complete — cite what you read and name the gaps.

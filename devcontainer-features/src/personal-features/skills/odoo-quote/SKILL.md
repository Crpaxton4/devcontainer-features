---
name: odoo-quote
description: Draft a scoped, line-item Odoo estimate or quote. Use when the user asks to quote, estimate, scope, size, price, or bid an Odoo change request, feature, or project — produces per-line-item hours with assumptions, exclusions, and a risk buffer, grounded in the project's real Odoo context.
---

<!-- feature-managed; overwritten on container create — edit in the devcontainer-features repo (src/personal-features/skills/odoo-quote/). -->

# Odoo scoping and quote drafting

Turn a fuzzy request into a defensible, line-item estimate. This is a
consulting deliverable — it complements the `odoo-sdk` `implement_task` prompt
(which executes tracked work); it does not start or log anything. All tools
below are the read-only `odoo-sdk` MCP tools.

## 1. Gather context before you estimate (read-only)

Never estimate from the request text alone. Pull the real context first:

- `search_projects(query="<client or project name>")` → get the `project.project`
  id. Confirm you have the right project before going deeper.
- `search_tasks(query="<feature keyword>", project_id=<id>)` → locate the
  driving task(s).
- `get_task(task_id=<id>, include=["description", "chatter"])` → read the full
  ask and the conversation that shaped it. Add `"dependencies"` and `"subtasks"`
  if the task is part of a larger tree.
- `search_chatter(query="<feature keyword>", model="project.task", date_from="YYYY-MM-DD")`
  → surface prior discussion, decisions, and constraints across tasks.
- `get_task_attachments(task_id=<id>)` then
  `read_attachment(attachment_id=<id>, mode="text")` → read specs, mockups, or
  requirement docs the client attached.
- `search_knowledge_articles(query="<module or rate card>")` +
  `read_knowledge_article(article_id=<id>)` → reuse prior estimates, standard
  rates, and known effort for similar work (Enterprise-only; skip if it errors).

If context is thin, say so and list what you still need — do not paper over it
with padding.

## 2. Decompose scope into line items

Break the work into estimable line items. A typical Odoo change spans several
of: data model changes, view/UI work, business logic, security (access +
record rules), reports/printouts, integrations, data migration, configuration,
testing/QA, UAT support, documentation, and project management. One line = one
independently-estimable unit of work.

## 3. Estimate each line item

- Give hours per line item. Where a line is uncertain, use a three-point
  estimate (optimistic / likely / pessimistic) and carry the likely figure.
- Base numbers on the pulled context and comparable past work — cite the task
  id or knowledge article you leaned on.
- Keep PM/QA/UAT as their own lines (commonly 10–20% and 15–25% of build effort
  respectively) rather than hiding them inside dev lines.

## 4. Apply a risk buffer

Add an explicit contingency line sized to the unknowns, not a reflex 10%:

- Low risk (well-understood, standard config): ~10%.
- Medium (custom logic, some unknowns): ~20%.
- High (integration, migration, vague requirements, unfamiliar module): 30%+.

State *why* the buffer is what it is.

## 5. Write assumptions and exclusions

- **Assumptions** — every condition the estimate depends on (Odoo version and
  edition, data quality, API/environment access, client availability for UAT,
  no third-party module conflicts, scope frozen at these line items).
- **Exclusions** — explicitly out of scope (training, data cleansing, unrelated
  bugs, production hosting, ongoing support) so scope creep is visible.

## Output template

```
# Quote — <client / project> — <date>
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

## Where human judgment is required (flag, don't fabricate)

- Hourly/day rates, discounts, payment terms, and fixed-price vs T&M — the
  owner sets these commercially. Present hours; leave money to the human unless
  a rate was given.
- Final sign-off and any client-facing commitment.

## Do-not

- Never invent hours to fill a table — an unknown is an open question, not a
  guess dressed as a number.
- Never issue a quote without assumptions and exclusions.
- Do not commit to a fixed price when scope is fuzzy; recommend T&M or a paid
  discovery instead.
- Do not treat pulled context as complete — cite what you read and name the gaps.

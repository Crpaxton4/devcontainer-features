---
name: discovery-notes
description: "Capture and structure client discovery for an Odoo engagement, mining Odoo chatter and knowledge articles first. Use for a discovery or requirements call, current process, actors, volumes, integrations, pain points, or gap analysis before scoping."
user-invocable: false
---
# Client discovery capture

Turn a discovery conversation into structured notes: what the client does today, who
does it, how much, what hurts, and where Odoo fits. Every tool below is read-only.

## 1. Mine the records first

Read the records before you list any question as open.

- `search_chatter(query="<client / topic keyword>")` → prior conversations, stated
  requirements, complaints. Narrow with `model="project.task"` or `date_from="YYYY-MM-DD"`.
- `search_knowledge_articles(query="<process or client>")` then
  `read_knowledge_article(article_id=<id>)` → process docs and prior discovery
  (Enterprise only; skip on error and name the lost source).
- `search_projects(query=...)` then `get_task(task_id=<id>, include=["description",
  "chatter"])` → the tasks that frame the engagement.

List what the records answered as confirmed facts and what they did not as open questions.

## 2. Question checklist

Work every group. Capture answers verbatim where wording matters. Mark each answer you
did not get as `UNKNOWN — follow up`.

### Current process
- Walk the end-to-end process step by step. What triggers it? What ends it?
- Which steps are manual, spreadsheet-based, or outside any system today?

### Actors and roles
- Who performs each step? Which Odoo groups or departments do they map to?
- Who approves? Who is accountable for the outcome?

### Volumes and frequency
- How many transactions or records per day, week, month? Peak against average?
- Data volumes to migrate: records, documents, history depth?

### Systems and integrations
- Which systems are in play (Odoo modules and external)? Source of truth for each?
- Required integrations: data flows, direction, frequency (real-time or batch)?

### Pain points
- Where does it break, slow down, or cause rework or errors today?
- What is the cost of the pain: time, money, risk, compliance?

### Constraints and success
- Odoo version and edition, deadlines, budget signals, compliance needs.
- What does "done well" look like to the client? How will they measure it?

## 3. Current-process mapping

| Step | Actor | System/tool | Input | Output | Pain / risk |
|------|-------|-------------|-------|--------|-------------|

## 4. Gap analysis

| Need | Today (as-is) | Odoo standard? | Gap → config / custom / integration | Priority |
|------|---------------|----------------|-------------------------------------|----------|

Prefer standard Odoo. Flag each customization with why standard does not fit.

## Output template

```
# Discovery notes — <client> — <date>
Context reviewed: <chatter/articles/tasks pulled>

## Attendees & roles
## Current process (as-is)   <table>
## Volumes & data
## Systems & integrations
## Pain points (ranked)
## Gap analysis              <table>
## Constraints & success criteria
## Open questions / follow-ups
## Recommended next step
```

## Stop conditions

- Never invent an answer or infer a volume. Record `UNKNOWN` and follow up.
- Keep what the client stated apart from what you observed or assumed.
- Do not propose a solution mid-discovery. Capture the problem fully first.
- Keep the exact wording of requirements, compliance and success metrics.

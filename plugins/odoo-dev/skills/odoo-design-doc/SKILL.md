---
name: odoo-design-doc
description: "Write an Odoo solution or technical design document, discovering current state via read-only Odoo tools first. Use to design or spec an Odoo feature, module, or customization: models, fields, views, security, migration, upgrade impact, rollout."
user-invocable: false
---
# Odoo solution design document

A technical design an Odoo developer can build from and a client can approve. Write it
only when the request asks for a design document. Every tool below is read-only.

## 1. Discover current state first

- `search_projects(query=...)` then `get_task(task_id=<id>, include=["description",
  "chatter", "dependencies"])` → the requirement and the decisions behind it.
- `search_chatter(query="<model or feature>", model="project.task")` → prior
  decisions, rejected approaches, constraints.
- `get_task_attachments(task_id=<id>)` then
  `read_attachment(attachment_id=<id>, mode="text")` → existing specs, ERDs, mockups.
- `search_knowledge_articles(query=...)` then `read_knowledge_article(article_id=<id>)`
  → architecture notes and conventions (Enterprise only; skip on error).

Record the target Odoo version and edition first. They decide which APIs are valid and
whether Enterprise models exist.

## 2. Template

Fill each section. Use real technical names everywhere: model `model.name`, field
`field_name`, XML ids. Mark a name you propose as *proposed*.

### Overview
- Problem statement, goal and success criteria, one paragraph.
- In scope and out of scope.

### Data model
- Model: `technical.model.name`, new or extended through `_inherit`.
- Fields table: technical name · type · required · default · compute or related (with
  `@api.depends`) · help. Mark `store=True` where a computed field is searched or grouped.
- Relationships and `ondelete` behaviour for Many2one fields.

### Views and UX
- Views to add or extend: form, list, search, kanban, pivot, graph.
- Menu items and window actions, with the model and the view mode.
- For an extension, the inherited view and the `xpath` and `position`.

### Business logic
- Methods and their triggers: button, `@api.model`, automation, cron.
- Computed and related fields, `@api.constrains`, `@api.onchange`.
- Automated or server action against code: state which and why.

### Security
- Access rights (`ir.model.access.csv`): per group, the read/write/create/unlink matrix.
- Record rules (`ir.rule`): domain, groups, global or group-specific, multi-company.
- New security groups and their inheritance.

### Data migration
- What existing data moves or is backfilled, and its source.
- Approach: pre/post migration scripts, `noupdate` data, or a one-off script.
- Idempotency and recovery from a partial run.

### Upgrade impact
- Version-specific APIs used and deprecations on the target version.
- Impact on existing customizations and third-party modules.
- Whether the design survives the client's next planned upgrade.

### Rollout plan
- Environments: dev → staging/UAT → production.
- Steps: module install or upgrade, data load, config, smoke test.
- Rollback plan per risky step: backup point, reverse migration.
- UAT owner and go/no-go criteria.

### Open questions
- Every unresolved decision, with who must answer it.

## Stop conditions

- Never invent a technical name that can already exist. Derive it from the records.
- Never skip security. A design without access rights and record rules is incomplete.
- Never use an API without the version it requires or was removed in.
- Never present the design as final while open questions remain. List them.

---
name: odoo-design-doc
description: Write an Odoo solution/technical design document. Use when the user asks to design, spec, or write a technical or solution design for an Odoo feature, module, or customization — covering models and fields (with technical names), views, security (access rights + record rules), data migration, upgrade impact, and rollout. Discovers current state via read-only Odoo tools first.
---

<!-- feature-managed; overwritten on container create — edit in the devcontainer-features repo (src/personal-features/skills/odoo-design-doc/). -->

# Odoo solution design document

Produce a technical design an Odoo dev can build from and a client can approve.
This is a consulting deliverable — it complements, and does not replace, the
`odoo-sdk` `implement_task` execution prompt. All tools below are read-only
`odoo-sdk` MCP tools.

## 1. Discover current state first (read-only)

Design against what exists, not what you assume exists:

- `search_projects(query=...)` → `get_task(task_id=<id>, include=["description",
  "chatter", "dependencies"])` for the requirement and the decisions behind it.
- `search_chatter(query="<model or feature>", model="project.task")` → prior
  decisions, rejected approaches, and constraints.
- `get_task_attachments(task_id=<id>)` +
  `read_attachment(attachment_id=<id>, mode="text")` → existing specs, ERDs,
  mockups.
- `search_knowledge_articles(query=...)` +
  `read_knowledge_article(article_id=<id>)` → existing architecture notes and
  conventions (Enterprise-only; skip on error).

Record the target **Odoo version and edition** up front — it drives API
choices, deprecations, and whether Enterprise-only models are available.

## 2. Design document template

Fill each section. Use real Odoo **technical names** everywhere (model
`model.name`, field `field_name`, XML ids) — never marketing labels alone.

### Overview
- Problem statement, goal, and success criteria (1 paragraph).
- In scope / out of scope.

### Data model
For each new or extended model:
- Model: `technical.model.name` (new, or extends existing via `_inherit`).
- Fields table: technical name · type (Char/Many2one/…) · required · default ·
  compute/related (with `@api.depends`) · help. Mark `store=True` where a
  computed field is searched or grouped.
- Relationships and `ondelete` behavior for Many2one fields.

### Views and UX
- Views to add/extend: form, list (tree), search, kanban, pivot/graph.
- Menu items and window actions (with the model and view mode).
- For extensions, name the view being inherited and the `xpath`/`position`.

### Business logic
- Methods and their triggers (button, `@api.model`, automation, cron).
- Computed/related fields, `@api.constrains` validations, `@api.onchange`.
- Automated actions / server actions vs. code — state which and why.

### Security
- **Access rights** (`ir.model.access.csv`): per group, the read/write/
  create/unlink matrix for each model.
- **Record rules** (`ir.rule`): the domain, the groups it applies to, and
  whether global or group-specific. Spell out multi-company rules if relevant.
- New security groups and their inheritance.

### Data migration
- What existing data must move or be backfilled, and its source.
- Approach: pre/post migration scripts, `noupdate` data, or a one-off script.
- Idempotency and how a partial run is recovered.

### Upgrade impact
- Version-specific APIs used and any deprecations on the target version.
- Impact on existing customizations and third-party modules.
- Whether this design survives the client's next planned Odoo upgrade.

### Rollout plan
- Environments: dev → staging/UAT → production.
- Steps: module install/upgrade, data load, config, smoke test.
- **Rollback plan** for each risky step (backup point, reverse migration).
- UAT owner and go/no-go criteria.

### Open questions
- Every unresolved decision, with who must answer it.

## Do-not

- Do not invent field or model technical names that may already exist — derive
  them from discovered context, and mark any you are proposing as *proposed*.
- Do not skip security: an Odoo design without access rights and record rules is
  incomplete.
- Do not use APIs without noting the version they require or were removed in.
- Do not present the design as final while open questions remain — list them.

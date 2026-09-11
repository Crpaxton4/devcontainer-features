---
name: odoo-code-review
description: "The Odoo domain lens over module and addon code, in addition to a generic review and never instead. Covers ORM anti-patterns, sudo() misuse, raw-SQL injection, N+1 and prefetch, ir.model.access.csv, record rules, upgrade safety, untranslated strings."
user-invocable: false
---

<!-- Packaged copy: THIS file (odoo_sdk/skills/odoo-code-review/SKILL.md) is the
     source of truth for the skill body — edit it HERE. The plugin and any
     other synced copies are generated from it via `odoo-sdk sync-skills`. -->

# Odoo code review checklist

Odoo-specific review lens. Use this *alongside* the generic `/code-review`
skill — it adds Odoo domain knowledge; it does not call or replace that flow,
and it does not run tracked work (that is `implement_task`'s job). Establish the
target **Odoo version and edition** first: it changes which APIs are valid.

Review the diff against every group below. Flag the file and line; cite the
version rule when one applies.

## When to use

An Odoo module diff, addon, or customization is under review and the generic review has already run or is running alongside; someone asks whether an addon is safe to ship; sudo(), raw SQL, a search() inside a loop, or a missing ir.model.access.csv entry turns up in the diff; or a module is being checked for upgrade safety before a version bump.

## ORM anti-patterns
- Writes/creates inside a loop over records — batch instead: build a list of
  vals and `create([...])`, or `recordset.write({...})` once.
- `search()` or `browse()` inside a loop — pull the data once, then work on the
  recordset (`recordset.filtered(...)`, `.mapped(...)`).
- Reading a related field per-record in a loop instead of `mapped()` /
  `read_group()` for aggregates.
- Mutating a recordset while iterating it.
- Using `.id`/`.ids` where a recordset is expected (or vice versa).
- Business logic bypassing the ORM so computes, constraints, and rules never fire.

## N+1 and prefetch
- Loops that defeat Odoo's prefetching (e.g. `record.partner_id.name` fetched
  one record at a time after breaking the recordset apart).
- Aggregations done in Python that `read_group` does in one query.
- Computed field without `store=True` that is searched, grouped, or sorted on.
- Missing/incorrect `@api.depends` — stale computes or over-recomputation.
- Missing DB index (`index=True`) on frequently-searched fields.

## sudo() misuse
- `sudo()` used to dodge an access error instead of fixing access rights /
  record rules — it silently bypasses record rules and is a privilege-escalation
  and data-leak risk.
- `sudo()` spanning more than the minimal operation that truly needs it.
- Writing user-supplied data under `sudo()` without re-checking authorization.
- Using `sudo()` where `with_company` / `with_context` was the real intent.

## Raw SQL / injection
- `self.env.cr.execute(...)` with f-strings or `%`/`.format()` string building —
  SQL injection. Parameterize: `cr.execute("... WHERE id = %s", (value,))`.
- Table/column names interpolated from input.
- Raw SQL that skips ORM access rules/record rules where the ORM should be used;
  if raw SQL is justified (performance), confirm the security implication is
  understood and note it.
- Reads bypassing multi-company or record-rule scoping.

## Security: access rights & record rules
- New model without an `ir.model.access.csv` entry (defaults to no access, or
  someone will "fix" it with a too-broad grant).
- Over-broad access (write/unlink to `base.group_user`) where a narrower group
  fits.
- Missing `ir.rule` record rules for multi-company or per-user/-team data
  isolation; check the rule domain actually scopes what it claims.
- Menus/actions exposed without a matching `groups` restriction.

## Upgrade safety
- Deprecated/removed decorators and APIs for the target version (e.g.
  `@api.one` removed in 12.0; `@api.multi` a no-op from 13.0 and removed in
  14.0; the pre-8.0 `_columns`/`_defaults` dicts). Confirm each against the
  actual target version before flagging.
- Hardcoded database ids instead of `self.env.ref('module.xml_id')`.
- Data records missing `noupdate` where they must survive module upgrade.
- Field/model renames without a migration script — data loss on upgrade.
- Overrides of core methods whose signature changed across versions.
- Manifest: correct `depends`, `version`, and no stale/removed dependencies.

## Translations
- User-facing strings not wrapped in `_()` (imported from `odoo.tools.translate`
  / `odoo._`).
- f-strings or concatenation **inside** `_()` — breaks extraction. Use
  `_("Hello %s") % name` or `_("Hello %(name)s", name=name)`.
- Field `string`/`help` and error messages left untranslated where the client is
  multilingual.

## Views & QWeb
- XML view/field references to models or fields that do not exist.
- Unescaped `t-raw` / unsafe QWeb rendering of user data (XSS).
- Inherited views without a robust `xpath` (brittle position selectors).

## Function length
- A function longer than **25 logical lines of code** is a finding. One statement
  is one logical line regardless of how it is wrapped, and comments do not count.
- The limit is a hard ceiling rather than a target: past a screenful the reviewer
  can no longer hold the whole function in working memory and has to reassemble it
  from fragments while scrolling.
- When flagging one, name the natural join point between distinct responsibilities
  where a helper should be extracted. Long `create`/`write` overrides and compute
  methods that inline several steps are the usual offenders in Odoo code.

## Do-not

- Do not report generic style issues this checklist does not cover — that is the
  generic `/code-review`'s job; keep this review Odoo-specific.
- Do not flag an API as wrong without naming the version rule; "removed in 13.0"
  is a finding, "looks old" is not.
- Do not approve `sudo()` or raw SQL because "it works" — state the security
  implication explicitly.
- Do not invent the Odoo version; if unknown, ask, and scope version-specific
  findings as conditional.

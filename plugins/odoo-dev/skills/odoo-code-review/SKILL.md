---
name: odoo-code-review
description: "The Odoo domain lens over module and addon code, in addition to a generic review and never instead. Covers ORM anti-patterns, sudo() misuse, raw-SQL injection, N+1 and prefetch, ir.model.access.csv, record rules, upgrade safety, untranslated strings."
user-invocable: false
---
# Odoo code review checklist

State the target Odoo version and edition first. They decide which APIs are valid.
Review the diff against every group below. Flag the file and the line; cite the version
rule when one applies.

## ORM anti-patterns
- Writes or creates inside a loop over records. Batch: build a list of vals and
  `create([...])`, or `recordset.write({...})` once.
- `search()` or `browse()` inside a loop. Pull the data once, then `filtered` or `mapped`.
- A related field read per record in a loop instead of `mapped()` or `read_group()`.
- A recordset mutated while iterated.
- `.id` or `.ids` where a recordset is expected, or the reverse.
- Business logic that bypasses the ORM, so computes, constraints and rules never fire.

## N+1 and prefetch
- Loops that defeat prefetch (`record.partner_id.name` one record at a time after
  breaking the recordset apart).
- Aggregations in Python that `read_group` does in one query.
- A computed field without `store=True` that is searched, grouped or sorted on.
- Missing or wrong `@api.depends`: stale computes or over-recomputation.
- No `index=True` on a frequently searched field.

## sudo() misuse
- `sudo()` used to dodge an access error instead of fixing access rights or record
  rules. It bypasses record rules: privilege escalation and data leak.
- `sudo()` spanning more than the minimal operation that needs it.
- User-supplied data written under `sudo()` without a re-check of authorization.
- `sudo()` where `with_company` or `with_context` was the real intent.

## Raw SQL and injection
- `self.env.cr.execute(...)` with f-strings, `%` or `.format()`. Parameterize:
  `cr.execute("... WHERE id = %s", (value,))`.
- Table or column names interpolated from input.
- Raw SQL that skips access rules where the ORM fits. Justified raw SQL names its
  security implication.
- Reads that bypass multi-company or record-rule scoping.

## Security: access rights and record rules
- A new model without an `ir.model.access.csv` entry.
- Over-broad access (write or unlink to `base.group_user`) where a narrower group fits.
- Missing `ir.rule` for multi-company or per-user data. The domain must scope what it claims.
- Menus or actions exposed without a matching `groups` restriction.

## Upgrade safety
- Deprecated or removed decorators and APIs for the target version (`@api.one` removed
  in 12.0; `@api.multi` a no-op from 13.0 and removed in 14.0; pre-8.0 `_columns`).
  Confirm each against the target version before you flag it.
- Hardcoded database ids instead of `self.env.ref('module.xml_id')`.
- Data records without `noupdate` where they must survive a module upgrade.
- A field or model rename without a migration script.
- Overrides of core methods whose signature changed across versions.
- Manifest: correct `depends` and `version`, no stale dependency.

## Translations
- User-facing strings not wrapped in `_()`.
- f-strings or concatenation inside `_()`. Use `_("Hello %(name)s", name=name)`.
- Field `string` and `help` and error messages left untranslated for a multilingual client.

## Views and QWeb
- View or field references to models or fields that do not exist.
- `t-raw` or unsafe QWeb rendering of user data (XSS).
- An inherited view with a brittle `xpath` (position selectors).

## Function length
- A function above 25 logical lines of code is a finding. One statement is one logical
  line; comments do not count.
- Name the join point between responsibilities where a helper comes out. Long `create`
  and `write` overrides and compute methods are the usual offenders.

## Output

One line per finding: `file:line · group · severity · what is wrong · version rule`.
Severity is `blocker`, `high`, `medium` or `low`.

## Stop conditions

- Report nothing this checklist does not cover. Keep the review Odoo-specific.
- Never flag an API as wrong without the version rule. "Removed in 13.0" is a finding;
  "looks old" is not.
- Never approve `sudo()` or raw SQL because it works. State the security implication.
- Never invent the Odoo version. When it is not given, state the assumption and mark
  version-specific findings conditional.

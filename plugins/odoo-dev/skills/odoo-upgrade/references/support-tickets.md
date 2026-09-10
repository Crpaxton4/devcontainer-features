# Odoo Support Tickets (Upgrade)

When upgrade platform fail — not your code — ticket is project artifact, not side errand. Pending platform issue = go-live blocker (sop.md 6.6, 7.2). Needs id, state, place to look up.

## Channel

| Situation | Where |
|---|---|
| Testing an upgrade | <https://www.odoo.com/help?stage=migration> — "An issue related to my future upgrade (I am testing an upgrade)" |
| After production go-live | <https://www.odoo.com/help?stage=post_upgrade> — "An issue related to my upgrade (production)" |

Distinct queues. Wrong one delay answer. No documented `upgrade@odoo.com` address — all route through odoo.com/help.

## Subject

```
[upgrade][<db>][<target>] <one-line symptom>
```

Examples:

```
[upgrade][acme_prod][19.0] account.move.line upgrade fails on partial reconcile
[upgrade][acme_prod][19.0] stock.quant constraint violated during standard phase
```

Symptom, not diagnosis. "Fails on X" get routed. "Your migration script is wrong" get argued with.

## Body

```markdown
Upgrade request token: <token>
Database UUID: <database.uuid from ir_config_parameter>
Subscription code: <database.enterprise_code>
Source version: <from>   Target version: <target>
Hosting: on-premise (Enterprise)   PostgreSQL: <version>

## What happened
<one paragraph: which phase, what stopped>

## Traceback
```
<the full traceback, verbatim>
```

## Minimal reproduction
<the smallest thing that reproduces it — ideally on a blank database of the
source version — or an explicit statement that it only reproduces on customer
data, and why>

## What we ruled out
- Custom modules: <how you established they are not involved, e.g. the failure
  is in the standard phase, before custom modules load>
- <anything else eliminated>
```

Get identifiers with:

```bash
psql -Atc "select key, value from ir_config_parameter
           where key in ('database.uuid','database.enterprise_code')" <db>
```

## Odoo support is not a client surface

Published-surface rule — single extracted error line, never raw log — govern **PR bodies and Odoo chatter**. Customer read those.

Support ticket is opposite: go to engineers who need whole thing. **Paste the full traceback.** Attach `upgrade.log` and `upgrade-report.html`. Withholding them = most common reason ticket take three round trips instead of one.

Keep out credentials only — subscription code and database UUID belong in ticket, database passwords and API keys do not.

## The minimal repro is the leverage

Traceback alone get "please provide a reproduction". Before filing:

1. Try on **blank database of source version**, only standard modules involved. Reproduces? Say so, give steps — that platform bug, move fast.
2. Reproduces only on customer data? Say so explicit, name records: `fails on account.move.line rows where partial reconcile spans a
   closed period (≈40 rows, ids attached)`.
3. Say which custom modules installed, and whether failure happen before they load. Failure in standard phase, before custom code = strongest framing.

## Register

Keep `tickets.csv` in project working directory next to inventory artifacts. `build_workbook.py --tickets` fold it in as workbook sheet.

```csv
id,date,subject,token,status,blocking_module,resolution,link
```

| Column | Content |
|---|---|
| `id` | Odoo ticket number once assigned; blank until then |
| `date` | Filed, `YYYY-MM-DD` |
| `subject` | Exactly subject line above — make greppable |
| `token` | Upgrade request token ticket is about |
| `status` | `open` / `waiting-odoo` / `waiting-us` / `resolved` / `waived` |
| `blocking_module` | Module whose upgrade it blocks, or `platform` |
| `resolution` | One line once closed. `waived` needs written waiver referenced |
| `link` | URL to ticket |

`waived` is real state, needs waiver **in writing** (sop.md 7.2). Every ticket must be resolved or waived before production upgrade scheduled.

## Do not `wipe` while a ticket is open

```
Since this command is a destructive action both a token and the associated
contract are mandatory. All associated requests of the contract will be wiped out.
```

`wipe` remove dumps behind every request on contract. Client's own warning: it "will make it impossible to get any support for what happened during the upgrade". Never run while ticket reference one of those requests.
# Prior-Art Method

Two questions. Ask on every requested feature before anyone estimate build:

1. **Native review** — target Odoo standard (community **or** enterprise) already do this?
2. **OCA review** — OCA module on customer target series already do this?

`no` to both fine, common answer. But must be *earned*.

Why it lead: custom module cost build once, then cost port at every future upgrade, forever. Behaviour answered by settings checkbox or maintained OCA module cost one config click, never show up in next upgrade. Fresh-database project: these two questions decide most of scope. In-place upgrade: they cut port list.

## Rule: verdicts come from greps, never from memory

Model knowledge of version that postdate its training unreliable. OCA repos move every week. Every verdict cite path, catalog row, or fetched README. Unsupported `yes` send client into rebuild that not work. Unsupported `no` bill client for thing they already own.

Corollary: also check if target version **removed** what feature would extend (dropped payment provider, deleted field, renamed model). Not native answer — build hazard. Say so.

## Scoping the question

Answer prior art per **capability**, not per module. Split request into 2–5 concrete, user-facing capabilities first, in user words:

> "Salesperson sees the customer's carrier account number on the delivery order"
> "Delivery orders are split by requested date"

Module-level verdict ("partly native") tell nobody what to build. Capability-level verdict do, and map straight onto quote line items and design-doc scope.

## Native review

Method, in order:

1. List what capability need: fields, methods, views, buttons, crons, reports, controllers.
2. Grep target community **and** enterprise trees per item — model names, field names, xmlids, **and functional synonyms**. Trees from `addons_paths.sh`. Grep patterns and roots per `odoo-devcontainer/references/searching.md`.
3. Search **neighbour** modules, not only obvious one: `sale`, `sale_stock`, `stock`, `mrp`, `mrp_workorder`, `account`, `delivery_*`, `payment_*`, `mail`, `web`, `base`.
4. Check `res.config.settings` — lot of "custom" behaviour is settings toggle in later series.
5. Check if target **removed** something request depend on.

Verdict format, one line per capability:

- `native: no` — target standard have nothing equivalent.
- `native: yes/partial: <how/where in target>` — name target module, feature, or setting. Examples: `yes/partial: mail composer has cc/bcc fields`, `yes/partial: stock.picking has date_done`, `yes/partial: account 'send & print' wizard`.

Evidence: at least one path in target tree, or exact grep terms tried when answer `no`. "I looked" not evidence.

Ask same question of vendor and OCA modules customer already run: bought app that target version absorbed is *merge into standard*, not *replace*.

## OCA catalog

```bash
python3 <base directory>/scripts/oca_check.py /mnt/extra-addons --series 16.0,17.0,18.0,19.0 --csv inventory.csv
python3 <base directory>/scripts/oca_catalog.py --series 19.0 -o oca19_catalog.csv
```

`oca_check.py` (run first) cache one branch listing per repo. `oca_catalog.py` reuse those listings and fetch each module manifest through batched GitHub GraphQL (~60 modules/request, ~25 requests for full 19.0 org). CSV columns: `repo, module, name, summary, description, depends, license, installable, development_status`. Expect ~1,300 rows on mature series. Re-runs only fetch what missing.

Both need authenticated `gh`. `oca_check.py` exit **2** on `PARTIAL SCAN` (network trouble, or >10% of repos failing) and leave CSV untouched — only full healthy scan prove absence, so never report partial scan as `none`.

Catalog is search space. Grep it — beat asking model what OCA have.

## OCA review

Method per capability:

1. Grep catalog with **3+ keyword variants**. OCA name things own way: `cc`/`bcc`/`carbon copy`; `split`/`delivery`/`date`; `tracking`/`audit`/`log`; `shipping account`/`carrier account`.
2. Also search **by dependency** (`grep ';mrp_workorder' catalog.csv`) — what module depend on is stronger signal than how it worded its summary.
3. Shortlist, then **fetch README of every candidate you name**. Never judge from module name:
   - `https://raw.githubusercontent.com/OCA/<repo>/<target>/<module>/README.rst`
   - `https://raw.githubusercontent.com/OCA/<repo>/<target>/<module>/__manifest__.py`
4. Decide. Record catalog rows and READMEs checked — or keywords tried, when answer `none`.

Verdict format:

- `oca: none` — no target-branch OCA module cover meaningful part.
- `oca: <repo>/<module> (full)` — deliver what request deliver for end user (configuration may differ).
- `oca: <repo>/<module> (partial)` — cover part. **Say what remain** — that remainder is thing being quoted.
- Strong fit exist only on previous series: `<repo>/<module> (18.0 only, port pending)`. Port cheaper than rewrite.
- Customization *is* OCA module: `self: OCA/<repo> <target>` when target port exist; else name successor; else `none on <target>`.

Watch `installable` and `development_status` in catalog. Alpha or Beta OCA module still candidate, but client must be told — and OCA own policy: Stable module may depend only on Stable and Mature modules. Put status in notes.

## Output

One block per capability, nothing that not traceable to path or row:

```
Capability: <one line, user's words>
  native: no | yes/partial: <where in target>
    evidence: <path(s) grepped, or the terms tried>
  oca: none | <repo>/<module> (full|partial)
    evidence: <catalog rows, READMEs fetched, or keywords tried>
    gap: <what is still missing, when partial>
  hazard: <target removed/renamed something this depends on>   (omit when none)
```

Then one recommendation per capability: **adopt** (native or OCA cover it), **adopt + delta** (OCA partial, build gap), or **build** (nothing cover it).

## Feeding the decision

| native | oca | Recommendation |
|---|---|---|
| `yes/partial` covering all | anything | **adopt native** — configure, not build |
| `no` | `(full)` | **adopt OCA** — install and configure |
| `no` | `(partial)` | **adopt + delta** — size gap before quote it |
| `no` | `none` | **build** — this the quotable work |
| is an OCA module, `self:` present | — | **adopt upstream** after diff local copy for local patches |

Native and OCA both cover behaviour: prefer native. One less dependency to carry through next upgrade.

Both columns are proposals for human review, not decisions. Say so when hand over.
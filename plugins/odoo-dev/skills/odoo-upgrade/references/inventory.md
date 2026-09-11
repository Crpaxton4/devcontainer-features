# Module Inventory

Inventory = first artifact of every upgrade project. Drives effort estimate, upgrade plan, per-department end-user test plan.

Standard deliverable = ONE xlsx, 4 sheets, no legend/README sheets (column meanings live here, in skill):

| Sheet | Rows | Built by |
|-------|------|----------|
| Module Inventory | 1/module | this file |
| Functional Requirements | 1/requirement | [functional-requirements.md](./functional-requirements.md) |
| Traceability | 1/module | derived — module → requirement IDs + counts + inventory verdicts |
| Inventory Evidence | 1/module | this file — paths/greps behind every verdict |
| Studio | 1/artifact | `studio_inventory.py` — optional, present only when studio.csv passed |
| Tickets | 1/ticket | `tickets.csv` — optional, see [support-tickets.md](./support-tickets.md) |

Three phases, each a fan-out of read-only agents writing JSON, then one merge script:

1. **Enrich** (this file) — per module: purpose, area, complexity, 3rd-party, action, `<major> native?`
2. **OCA/base review** ([oca-base-review.md](./oca-base-review.md)) — per module: `OCA <major> alternative`
3. **Requirements** ([functional-requirements.md](./functional-requirements.md)) — FR rows + Handled? verdict

Phase 2 read phase 1 output; phase 3 read both. Never reorder.

## Generate seed

```bash
python3 <base directory>/scripts/module_inventory.py [ADDONS_PATH ...] [--target-version 19.0] -o /tmp/inv/inventory.csv
python3 <plugin root>/skills/odoo-prior-art/scripts/oca_check.py [ADDONS_PATH ...] --series 16.0,17.0,18.0,19.0 --csv /tmp/inv/inventory.csv
python3 <base directory>/scripts/studio_inventory.py --db <production-copy> --csv /tmp/inv/studio.csv -o /tmp/inv/studio.json
```

Two directories are referenced above. `<base directory>` is the absolute path on the
`Base directory for this skill:` line injected above the `odoo-upgrade` skill body,
and `<plugin root>` is two levels above it: the base directory's parent is `skills/`,
and its parent is the plugin root. `oca_check.py` and `oca_catalog.py` now live in
the sibling skill at `<plugin root>/skills/odoo-prior-art/scripts`; contracts
unchanged.

Write both out as absolute paths in every command. A Bash call inherits no
environment and keeps no state from the call before it, so there is no variable to
set once and reuse. A person running these from a terminal substitutes their own
plugin root in the same place.

- Default `ADDONS_PATH` = `/mnt/extra-addons`; pass several dirs for one combined CSV.
- Stdlib only, no installs. Manifests parsed with `ast.literal_eval`, never imported.
- Unparseable manifest ⇒ row still emitted (LoC counted, purpose flagged `TODO-AI (manifest unparseable…)`) — fix manifest; never let module drop out of estimate silently.
- `--target-version` only affects generated apps.odoo.com fallback links.
- One work dir per project (e.g. `/tmp/<client>_inventory`) hold seed CSV + all agent JSON + catalog + `studio.csv` + `tickets.csv`. Everything downstream read from there.

## Studio inventory

Code inventory see only what is in git. Studio and other UI-built customization — `x_studio_*` fields, manual models, studio views, base automations, UI server actions and crons, UI reports — live in database rows, so a repo-only inventory silently reports zero. On a Studio-heavy customer that is the larger half of the scope.

```bash
python3 <base directory>/scripts/studio_inventory.py --db <production-copy> --csv studio.csv -o studio.json
```

Read-only (the connection itself is opened read-only), so it is safe against a restored copy. Run it against a COPY, never production.

Per-row `classification` is a proposal to sort a review, not a verdict:

| Classification | Meaning | Cost it implies |
|---|---|---|
| `convert-to-code` | Behaviour the customer depends on: `x_studio_*` fields, manual models, studio views/reports, unowned server actions and crons | Scaffold a real module, re-express each field as a real field, and write a migration script using upgrade-util `rename_field` to carry the existing column data across — see [migrations.md](./migrations.md). This is quotable work |
| `keep-as-data` | Legitimately data; migrates with the database (automations on standard models, UI-created records) | Verify after upgrade, no port |
| `purge` | Inactive/dead | Confirm, then drop |
| `review` | Not classifiable from the schema alone — notably **inactive views**, which are either abandoned work or an earlier upgrade casualty | A human looks |

`convert-to-code` rows belong in the estimate. A Studio field left as a manual field is re-created by hand after every upgrade and is invisible to code review forever.

## Columns (13)

| # | Column | Filled by | Semantics |
|---|--------|-----------|-----------|
| 1 | Module Name | script | `technical_name (Manifest display name)` |
| 2 | Module Purpose | script → AI | manifest `summary`, else first `description` line, else `TODO-AI`. AI refine vague ones from module code |
| 3 | Source version | script | manifest `version` verbatim. Series prefix hint hop count to target, but manifests go stale (never bumped since older series) — treat as hint, confirm against `$ODOO_VERSION`. No series prefix (`1.0`, `1.0.1`) reveal nothing: assume `$ODOO_VERSION` |
| 4 | LoC | script | Non-blank lines in `.py .xml .js .css .scss .csv`; skip `static/lib`, `node_modules`, `__pycache__`, `.po`; respect manifest `cloc_exclude` |
| 5 | Dependencies | script | manifest `depends`, `;`-joined, classified against devcontainer: bare = core, `[E]` = enterprise, `[C]` = local custom, `[?]` = not found anywhere. `[E]` rows need enterprise checkout on addons path to install/test; resolve every `[?]` before planning |
| 6 | 3rd party app? | script → AI | `Yes` = purchased/downloaded vendor or app-store module (`price` key, or clearly vendor product). `No` = in-house client code (whichever integrator wrote it) AND OCA community. `TODO-AI` = OPL-1 but no price: check `author`/`website` — many shops license in-house modules OPL-1 |
| 7 | 3rd party app link | script → AI | manifest `website`; priced modules fall back to `https://apps.odoo.com/apps/modules/{target}/{name}`. AI verify link point at actual app; if generic (bare domain, repo root) or network unavailable, keep manifest value as-is, flag in planning review |
| 8 | OCA repo | script → `oca_check.py` | Script seed `claimed — run oca_check.py` when manifest author contain `Odoo Community Association (OCA)` — claim, not proof (see below). `oca_check.py` replace with **verified** org location `OCA/<repo> (series,…)` or empty it |
| 9 | Complexity/risk | script → AI | Seed: LoC bands (<300 Low, <1500 Medium, else High) +1 level if custom JS or QWeb reports present. AI adjust after reading code (heavy ORM overrides, SQL, controllers ⇒ raise) |
| 10 | Upgrade action | script seeds → AI proposes → human confirms | Script seed `drop?` when manifest say `installable: False` (already dead — confirm, drop). One of: `keep` (port in-house code), `replace` (download vendor/OCA target release), `merge-into-standard` (target version cover natively), `drop` (dead/unused/superseded). Blank when genuinely unsure. Final call = human planning review |
| 11 | Functional area | AI | ONE bucket: Sales, CRM, Purchasing, Inventory, Manufacturing, Accounting, HR, Website/Portal, Reporting, Integration, Technical/Base. Drive per-department test planning; secondary areas go in Purpose text |
| 12 | `<major> native?` | AI (phase 1) | Does target-version standard (community **or** enterprise) do some/all of this? `no`, or `yes/partial: <how/where in target>`. Whole cell ≤ 50 chars — column is scanned, not read |
| 13 | `OCA <major> alternative` | AI (phase 2) | `none`, or `<repo>/<module> (full\|partial)`, `; `-separated, ≤ 3 candidates. See [oca-base-review.md](./oca-base-review.md) |

Header text carry target major: Odoo 19 project ⇒ `19 native?`, `OCA 19 alternative`. Build script derive both from `--target-version`.

## Inventory Evidence sheet

Verdict columns are short by design, so every one of them need a receipt. One row per module:

| Column | From | Content |
|--------|------|---------|
| Module | — | technical name |
| native? evidence | phase 1 | ≤ 300 chars. Path(s) in target tree, or `none found after grepping X, Y`. MUST cite ≥ 1 path or the grep terms tried |
| OCA alternative evidence | phase 2 | ≤ 300 chars. Catalog rows / READMEs checked, why fit or not. `none` ⇒ name keywords grepped |
| Vendor release | phase 1 | `yes` / `no` / `unknown` / `n/a` (n/a = in-house) — is there a target-series build of the vendor app? |
| Complexity rationale | phase 1 | ≤ 120 chars |
| Upgrade action rationale | phase 1 | ≤ 160 chars |
| Notes | phases 1+2 | port hazards, dead code, overlaps, hard-coded IDs, coverage gaps — `\|`-joined |

Evidence sheet = what makes verdicts reviewable. No evidence ⇒ verdict is a guess; re-run that module.

## OCA verification (`oca_check.py`)

```bash
python3 <plugin root>/skills/odoo-prior-art/scripts/oca_check.py [ADDONS_PATH ...] [--series 16.0,17.0,18.0,19.0] \
        [--csv inventory.csv] [-o oca_index.json] [--cache DIR] [--repos r1,r2]
```

Ground truth = module technical name exists as top-level dir with `__manifest__.py` on requested series branch of OCA addon repo (addon repo = org repo whose default branch is series number like `18.0`; master/main defaults = tooling; full forks `OCB`/`OpenUpgrade` excluded). Author string = corroboration only: modules hosted outside org carry it too, forks keep it after diverging. Org facts checker rely on: module name map to exactly ONE repo per series (verified org-wide), repo can differ between series; series branches pre-created as module-free skeletons, so branch existence ≠ module availability — only manifest check count.

Need authenticated `gh` + git: enumerate org repos via `gh api` (~3 requests), read branch contents from shallow blobless clones (no rate limits), cache listings by branch-tip SHA (re-runs near-instant). `--csv` fill `OCA repo` column in place. Full-org scan take minutes. On network trouble (nothing indexed, or >10% of repos failing) print `PARTIAL SCAN`, leave CSV untouched, exit 2 — absence only proven by full healthy scan.

Run it with ALL series in one pass (`--series 16.0,17.0,18.0,19.0`): same cost, and the branch listings it caches are the input of `oca_catalog.py` in phase 2.

`--repos` re-check specific repos: subset prove presence only, so *absence* mismatch suppressed (found-but-unclaimed still print), existing CSV cells never cleared.

Read `MISMATCH` lines it prints:
- *claims OCA but not found* — fork, renamed module, or module hosted outside org (e.g. partner's own GitHub): treat as custom code to port, not `replace`.
- *found but author doesn't claim OCA* — name collision: diff against OCA module before trusting match. Record the doubt in the cell (`[name match only; local copy = <lineage>, diff before replacing]`).
- Found on some series but not target series ⇒ OCA port not exist yet: `keep` (port it, consider contributing upstream) or wait.

## Phase 1 — enrichment fan-out

Read-only agents, ~10-12 modules each, grouped by functional area (same-area modules share target-version greps). Agents never edit addons, never run Odoo, never write memory, never spawn sub-agents. Each write ONE JSON array to its own path in the work dir: `enrich_g<N>.json`.

Give every agent: module dir, seed CSV path, and the source trees —

| Tree | Path (devcontainer) |
|------|---------------------|
| Target community | `/var/lib/odoo/src/odoo<major>/odoo/addons` (+ framework at `…/odoo/`) |
| Target enterprise | `/var/lib/odoo/addons/<target>/` |
| Source community (installed) | `/usr/lib/python3/dist-packages/odoo/addons/` |
| Source + intermediate enterprise | `/var/lib/odoo/addons/<series>/` |

Confirm paths exist before briefing — `odoo-dev:odoo-devcontainer` skill list what's checked out.

JSON keys, exactly: `module`, `purpose`, `functional_area`, `complexity`, `complexity_rationale`, `third_party`, `third_party_link`, `vendor_release`, `upgrade_action`, `upgrade_action_rationale`, `native`, `native_evidence`, `notes`. Semantics = columns 2/6/7/9/10/11/12 above + evidence sheet.

Method each agent follow (order matters — verdict come from greps, not memory):

1. Read `__manifest__.py`, `models/*.py`, `views/*.xml`, `wizard/`, `report/`, `static/src/**`, `security/`, `data/`.
2. Write the concrete customization list: fields added, methods overridden, views changed, buttons, crons, reports, controllers.
3. Per item grep target community + enterprise for equivalent — field names, model names, xmlids, and functional keywords. Check the target model AND neighbours (`sale`, `sale_stock`, `stock`, `mrp`, `mrp_workorder`, `account`, `delivery_*`, `payment_*`, `mail`, `web`, `base`), plus `res.config.settings` and list/toolbar features. Also check whether target REMOVED the thing the module extends (dropped payment provider, deleted field) — that's a finding, say so in evidence.
4. Decide `native` + evidence. `no` is a fine answer; unsupported `yes/partial` is not.
5. Decide remaining fields. Vendor-product check on `author`/`website` every row: free LGPL vendor modules carry no `price` key and masquerade as in-house code — vendor lineage means `replace` via vendor release, not in-house port. In-house OPL-1 ⇒ `third_party = No`.

`native` cell examples (all ≤ 50 chars): `yes/partial: mail composer has cc/bcc fields`, `yes/partial: stock.picking has date_done`, `yes/partial: account 'send & print' wizard`.

Agent reply back = one line per module `<module> | <native cell> | <upgrade_action>` — enough to spot a group that skipped the greps without reading its JSON.

## Build

```bash
python3 <base directory>/scripts/build_workbook.py --workdir /tmp/inv --target-version 19.0 \
        -o /mnt/extra-addons/<client>_upgrade_16_to_19_workbook.xlsx
```

Merge seed CSV + `enrich_*.json` + `oca_alt_*.json` + `fr_*.json` into the 4-sheet workbook (+ CSV siblings). Requirements JSON optional — absent ⇒ 2 sheets.

Script print a validation report and exit 1 when non-empty: modules missing enrichment or OCA rows, over-long `native?` cells, requirements without `shall` or carrying weak words, unknown source modules, modules with no requirement. Near-duplicate requirements print separately as a non-blocking review list. Read both and re-run the offending agents — it is the only check that the fan-out followed the brief.

## Interpretation for estimating

- Port effort per module ≈ LoC band × hop count (Source version → target), weighted by Complexity. JS-heavy and report-heavy modules dominate.
- Dependency depth matter: modules depended on by others port FIRST; broken base module block chain. Sort plan by topological order of Dependencies column.
- `3rd party app? = Yes` rows = separate workstream: **download target-version release** from vendor/store, don't port code. Effort = re-purchase/licence check + config re-validation + regression test. No target-version release ⇒ escalate to `Upgrade action` decision (port anyway / replace / drop).
- **OCA modules = `3rd party app? = No` but follow same replace-if-released logic** — externally maintained, never in-house code. Target series listed in `OCA repo` ⇒ `replace`; not listed ⇒ `keep` (port it, consider contributing upstream). Before setting `replace`, diff local copy against upstream series branch — local patches common; if diverged, port delta onto new release or upstream it (`oca-port` automate much of this).
- Rows where `<major> native?` is `yes/partial` or `OCA <major> alternative` is not `none` = candidates to NOT port at all. Biggest lever on total cost, especially on fresh-database projects — see [oca-base-review.md](./oca-base-review.md).
- Functional area column ⇒ group rows per department for end-user test plan; each `keep`/`replace` row need at least one user acceptance scenario in its area.

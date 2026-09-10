# Functional Requirements from Customizations

Phase 3 of the inventory workbook. Inventory answer "what modules exist"; requirements answer **"what did the business actually ask for"** — one row per discrete behaviour, traced to source modules, each marked whether target standard or an OCA target module already satisfy it, with a blank Decision column the client fill in.

Why bother when the inventory already has verdicts: a module is a bundle. Client can't drop "bista_sale_custom", but can absolutely drop "hold sales orders for engineering review" once they see it written out. Module-level verdicts are `yes/partial` mush; requirement-level verdicts are decisions. Expect ~4-5 requirements per module (117 modules ⇒ ~500 rows).

Run when: fresh-target-database project (client rebuild and adopt selectively), client want to pare down customizations, or upgrade cost need justifying behaviour by behaviour. Skip on a pure lift-and-shift port where nothing is up for debate.

## Writing rules

1. **Atomic** — one behaviour per requirement. Module doing 6 things ⇒ 6 rows. Two modules doing the same thing ⇒ ONE row, both in sources.
2. **`shall` only.** Never should / will / must / may.
3. **EARS syntax**, pick the pattern that fit:
   - Ubiquitous: `The system shall <action>.`
   - Event-driven: `When <trigger>, the system shall <action>.`
   - State-driven: `While <state>, the system shall <action>.`
   - Unwanted event: `If <condition>, the system shall <response>.`
   - Optional feature: `Where <feature/config>, the system shall <action>.`
   Name the actor when a role act: `When a sales user confirms a sales order, the system shall …`
4. **Implementation-neutral** — WHAT the business get, not HOW. No model/field/method/xmlid names in requirement text. Write `the system shall record the customer's carrier account number on the delivery`, not `add field ups_account_id on stock.picking`. Technical pointers go in Evidence.
5. **Testable** — a tester pass/fail it. Spell out numbers, document names, conditions. Ban weak words: fast, easy, user-friendly, efficient, appropriate, normal, few, most, timely, properly, reliable, intuitive.
6. **Consistent vocabulary** — same term everywhere: sales order, quotation, customer, vendor, purchase order, receipt (incoming transfer), delivery (outgoing transfer), transfer, manufacturing order (MO), work order, bill of materials (BoM), work center, vendor bill, customer invoice, credit note, payment, payment receipt, customer statement, contact, delivery address, invoice address, carrier, shipping account, product, lot/serial, report (printed PDF), list view, form view, chatter, internal note, email.
7. **Functional only** — skip porting/non-functional concerns ("compatible with 19", "uses OWL") and code-quality facts. Skip behaviour the module doesn't change (plain standard behaviour).
8. **Dead code still yield a requirement** — business asked for it once, and the client deserve to drop it knowingly rather than silently. Keep those rows brief, flag in Status.
9. **Vendor / OCA whole apps** (RMA, PrintNode, auditlog, accounting connectors, TaxJar): write the 5-12 capability-level requirements the client plausibly rely on — main flows, reports, integrations, settings — not every option. Where the repo can't prove the client use it, note `usage unverified`.

Rules 1-6 follow the standard requirements-writing guidance (QRA "7 tactics", Modern Requirements) — the point is a list a functional consultant can test and a client can veto, line by line.

## Columns

| Column | Semantics |
|--------|-----------|
| ID | `FR-###`, sequential, grouped by functional area. **Stable once issued — never renumber**; client annotate by ID |
| Requirement | The EARS statement, ≤ 300 chars |
| Type | Business rule \| Authorization \| User interaction \| Data processing \| Reporting/Notification \| Integration \| Backup/Recovery |
| Functional area | Sales \| CRM \| Purchasing \| Inventory \| Manufacturing \| Accounting \| HR \| Website/Portal \| Reporting \| Integration \| Technical/Base |
| Actor | Sales user \| Purchasing user \| Warehouse user \| Shop-floor operator \| Production planner \| Accountant \| Administrator \| Customer (portal) \| System (automatic) \| Any user |
| Source customization(s) | Module technical names implementing it; several when modules overlap |
| Status in source | `active` = works today; `dead` = never loads / uninstallable / unreachable; `broken` = loads but cannot work as written. Dead/broken = drop candidates |
| Handled? | `base <major>` \| `OCA` \| `no` — see rule below |
| Handled by | The named feature/module/path, ≤ 120 chars |
| Handled notes | Residual caveats (how the target equivalent differ in practice), ≤ 200 chars |
| Verification | Acceptance test a functional consultant run on the target build, ≤ 160 chars |
| Evidence | File / method / view pointer in the SOURCE module, ≤ 200 chars |
| Notes | Overlaps with other modules, hard-coded client data, business risk of dropping, ≤ 200 chars |
| Decision (Keep/Drop/Defer) | Left blank — client fill it |

## Handled? decision rule

Exactly three values:

- **`base <major>`** — target standard (community or enterprise) satisfy it AS WRITTEN with **configuration only**: settings, access groups, optional list columns, report options, standard wizards. NOT Studio, NOT automated actions, NOT custom views. Name the feature in Handled by.
- **`OCA`** — a module on the target OCA branch satisfy it (installable, verified in phase 2 / catalog). Handled by = `<repo>/<module>`.
- **`no`** — neither. Handled by name the cheapest path: `Studio field/view`, `OCA 18.0 only: <module>`, `open OCA PR <repo>#<n>`, `vendor <target> release: <app>`, `custom code`, `drop candidate`.

Both base and OCA satisfy ⇒ choose `base <major>` (one less dependency across the next upgrade).

**Split until each row has ONE unambiguous answer.** "Partially handled" is two requirements, not one hedge — that's what make the sheet decidable. Nuance that survive the split go in Handled notes.

## Phase 3 fan-out

Read-only agents, ~10-12 modules each, grouped by functional area so overlapping behaviours land in one agent and get merged instead of duplicated. Group assignment written once to `fr_groups.json` in the work dir (also the record of who did what).

Inputs per agent: module source; inventory CSV + `enrich_*.json` + `oca_alt_*.json` filtered to its modules (prior analysis — reuse it, but requirements come from the CODE); target community + enterprise trees; OCA catalog CSV. Grep the prior JSON by module name rather than loading whole files.

Each write ONE `fr_<GROUP>.json` array. Keys, exactly: `tmp_id`, `requirement`, `type`, `functional_area`, `actor`, `sources`, `evidence`, `status_source`, `status_note`, `handled`, `handled_by`, `handled_notes`, `verification`, `notes`.

`tmp_id` = `<GROUP>-<nn>` (`A-01`). Final `FR-###` assigned by the build script after sorting by area — agents never number globally, they'd collide.

Method: read the code and list every user-visible behaviour change (fields shown/hidden, buttons, validations, automatic actions, reports, emails, integrations, access rules, crons) → merge identical behaviours across the group's modules → write EARS statement, classify, decide Handled with a grep when prior evidence isn't specific enough for the requirement as written.

Before replying each agent validate its own file: JSON parses, every assigned module appear in some `sources`, no `should/must/will/may`, no weak words. Reply = count per Handled value + one line per requirement.

Execution economy (these agents are the expensive part of the project):

- No sub-agents. Agent do its own work.
- Read module files directly with offset/limit on files > 400 lines. Never `cat` several whole modules into one shell dump.
- Write the JSON as soon as the list is complete, validate, then reply.

## Merge, reconcile, build

```bash
python3 <base directory>/scripts/build_workbook.py --workdir /tmp/inv --target-version 19.0 \
        -o /mnt/extra-addons/<client>_upgrade_16_to_19_workbook.xlsx
```

Script sort by functional area, assign `FR-###`, emit Functional Requirements + Traceability sheets, and validate: missing/extra keys, bad Handled/Status/area values, requirements without `shall` or with weak words, unknown source modules, modules with no requirement, and print **near-duplicate requirements across groups** (token overlap ≥ 0.33) as a separate non-blocking review list.

Near-dups are the expected failure of any fan-out — two agents describing the same behaviour from two modules. Review each pair:

- Same behaviour ⇒ record in `fr_merges.json`: `{"<drop tmp_id>": {"keep": "<tmp_id>", "note": "…", "requirement": "<optional rewrite>"}}`. Script fold sources + evidence into the kept row and drop the other.
- Different behaviours that conflict or overlap in production (two modules printing the same report differently, three patching one layout) ⇒ `fr_notes.json`: `{"<tmp_id>": "note appended"}`, cross-referencing both IDs. Client need to see the conflict, not have it hidden.

Re-run the script after editing either file; it is idempotent. Non-empty problem list ⇒ exit 1. Fix or knowingly accept every line before delivering.

## Traceability sheet

Derived, one row per module: `Module | Requirement IDs | # reqs | # base <major> | # OCA | # no | Inventory: Upgrade action | Inventory: <major> native? | Inventory: OCA <major> alternative`.

This is where the two halves meet: module whose requirements are all `base <major>`/`OCA`, or all dropped by the client, need not be ported at all — whatever the inventory proposed. A module with requirements but `# reqs = 0` on another means the fan-out missed it; script flag that.

## Handing it over

Deliverable = the workbook, no legend or instruction sheets. Say in the message (not in the file): filter Functional Requirements by `Handled? = no` and `Status = active` to see what only custom code deliver today, fill Decision as Keep / Drop / Defer, and use Traceability to see which modules disappear once requirements are dropped. IDs are stable — annotate by ID.

Requirements list is an informational artifact for the client's decision, not a sign-off document: no owners, no dates, no evidence columns aimed at auditors.

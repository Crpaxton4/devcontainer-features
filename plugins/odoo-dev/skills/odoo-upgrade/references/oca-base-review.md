# OCA + Base Review — moved

This method now lives in the **`odoo-dev:odoo-prior-art`** skill, generalized from
inventory columns to per-capability verdicts so that quoting and design can use
it too:

- Method: `odoo-prior-art/references/method.md`
- Scripts: `odoo-prior-art/scripts/oca_check.py`, `oca_catalog.py`, `addons_paths.sh`

Nothing about the upgrade use changed. The two questions still fill two
inventory columns, and the mapping from those columns to an `Upgrade action` is
still in [inventory.md](./inventory.md):

| `<major> native?` | `OCA <major> alternative` | `Upgrade action` |
|---|---|---|
| `yes/partial` covering all | anything | `merge-into-standard` |
| `no` | `(full)` | `replace` |
| `no` | `(partial)` | `replace` + custom delta, or `keep` — size the gap first |
| `no` | `none`, in-house code | `keep` |
| `no` | `none`, vendor app | `replace` if the vendor ships a target release, else escalate |
| module is OCA, `self:` present | — | `replace` after diffing the local copy for local patches |

Cell formats for the two inventory columns (`native?` ≤ 50 chars, `OCA alternative`
≤ 80 chars, ≤ 3 candidates) are in [inventory.md](./inventory.md) columns 12–13.

Phase-2 fan-out for a whole-codebase inventory — one read-only agent per ~10-12
modules, each writing `oca_alt_g<N>.json` with keys `module`, `oca_alt`,
`oca_alt_evidence`, `coverage_gap`, `notes` — stays an inventory concern and is
described in [inventory.md](./inventory.md). `odoo-dev:odoo-prior-art` answers one
capability at a time; the inventory fan-out answers a whole codebase.

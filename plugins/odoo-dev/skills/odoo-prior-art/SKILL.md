---
name: odoo-prior-art
description: "Check whether standard Odoo on the target version, or an OCA module on the customer's series, already does this. Use at the START of any scoping, estimating, or design task, and for 'do we need to build this?'. Verdicts come from greps, never memory."
user-invocable: false
---
# Odoo Prior Art

Answer one question per capability: **does this already exist?** In standard Odoo on target version, or OCA module on customer's series.

Cheapest lever on quote. Custom module paid once when built, again at every upgrade. Settings checkbox or maintained OCA module paid once, never appear in next upgrade.

## When to use

Before any Odoo quote, estimate, design, or build begins; someone proposes a new custom module, model, or field; someone asks whether Odoo already does this, whether an OCA module exists for it, or whether it needs building at all; or an upgrade is being planned and a custom module may have been made redundant by the target series.

## The rule that makes it worth anything

**Verdicts come from greps and fetched READMEs, never from memory.** Model knowledge of post-training version unreliable. OCA repos change weekly. Every verdict cite path, catalog row, or README actually fetched. `no` and `none` respectable answers — unsupported `yes` send client into rebuild that not work, unsupported `no` bill them for thing they already own.

## Flow

1. **Resolve the series.** `odoo-dev:odoo-repo-map` give customer's `odoo_version`. For upgrade, target series matter, not one they run today.
2. **Get the trees to grep.**
   ```bash
   <base directory>/scripts/addons_paths.sh --series 19.0
   ```
   → `{"series","odoo_version","addons_paths":[],"community_path","enterprise_path","custom_paths":[],"missing":[]}`

   A non-empty `missing` is a **stop**, not a hint to grep harder: a `no` verdict from a tree that is not checked out is worthless. Say what is missing and ask for the checkout.
3. **Split the request into 2–5 concrete capabilities** in the user's own words. Prior art is answered per capability, not per module — "partly native" tells nobody what to build.
4. **Native review**, then **OCA review**, per `references/method.md`. Read it before doing either; it carries the grep discipline, the synonym rule, the neighbour-module list, and the exact verdict formats.
5. **Report** the verdict block and a recommendation per capability: **adopt**, **adopt + delta**, or **build**.

## Scripts

This skill's scripts (`PRIOR_ART_SCRIPTS`) live at `<base directory>/scripts`, where
`<base directory>` is the absolute path on the `Base directory for this skill:`
line injected above this body. The name is a label for that directory, not a shell
variable to set and reuse: every Bash call has to spell the absolute path out in
full, because a Bash call inherits no environment and keeps no state from the call
before it.

| Script | Use |
|---|---|
| `addons_paths.sh [--series V]` | Which trees to grep, and which are missing |
| `oca_check.py [PATH ...] --series 16.0,17.0,18.0,19.0 [--csv inventory.csv]` | Is a given local module actually an OCA module? Verified against the org, not the author string |
| `oca_catalog.py --series 19.0 -o catalog.csv` | Every OCA module on a series as one greppable CSV (~1,300 rows) |

Run `oca_check.py` **before** `oca_catalog.py`: the catalog reuses the branch listings the checker caches. Both need an authenticated `gh`.

`oca_check.py` exits **2** on `PARTIAL SCAN` — network trouble, or more than 10% of repos failing. A partial scan can prove presence but never absence, so it must never be reported as `none`.

## Output shape

```
Capability: <one line, in the user's words>
  native: no | yes/partial: <where in target>
    evidence: <path(s) grepped, or the terms tried>
  oca: none | <repo>/<module> (full|partial)
    evidence: <catalog rows, READMEs fetched, or keywords tried>
    gap: <what remains, when partial>
  hazard: <target removed or renamed something this depends on>
  -> adopt | adopt + delta | build
```

Client-facing output is normal prose. These verdicts are proposals for a human review — say so when handing them over.

## Where it feeds

| Consumer | What it takes |
|---|---|
| `odoo-dev:odoo-quote` | `adopt` capabilities become **exclusions/assumptions**, not line items; `adopt + delta` is quoted as the gap only |
| `odoo-dev:odoo-design-doc` | Build-vs-adopt per capability; adopted OCA modules become dependencies |
| `odoo-dev:odoo-upgrade` | The same two questions decide `keep` / `replace` / `merge-into-standard` per module — see its `references/inventory.md` |

## Reference

| File | Read when |
|---|---|
| [method.md](./references/method.md) | Before any native or OCA review — the grep discipline and verdict formats |

Grep roots and patterns: `odoo-devcontainer/references/searching.md`.
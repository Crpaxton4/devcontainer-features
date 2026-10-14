---
name: odoo-prior-art
description: "Check whether standard Odoo on the target version, or an OCA module on the customer's series, already does this. Use at the START of any scoping, estimating, or design task, and for 'do we need to build this?'. Verdicts come from greps, never memory."
user-invocable: false
---
# Odoo Prior Art

One question per capability: does this already exist, in standard Odoo on the target
series or in an OCA module on that series?

Verdicts come from greps and fetched READMEs, never from memory. Every verdict cites a
path, a catalog row, or a README that was read. `no` and `none` are respectable answers.

## Procedure

1. Take the series from the context you were given. For an upgrade, the target series
   is the one that matters.
2. Get the trees to grep:
   ```bash
   <base directory>/scripts/addons_paths.sh --series 19.0
   ```
   → `{"series","odoo_version","addons_paths":[],"community_path","enterprise_path","custom_paths":[],"missing":[]}`

   A non-empty `missing` is a stop. Stop; report the missing tree and the path expected.
   A `no` from a tree that is not checked out is worthless.
3. Split the request into 2 to 5 concrete capabilities, in the user's own words. Prior
   art is answered per capability, never per module.
4. Native review, then OCA review, per `references/method.md`. Read it before either:
   it carries the grep discipline, the synonym rule, the neighbour-module list and the
   verdict formats.
5. Report the verdict block and one recommendation per capability: adopt, adopt + delta,
   or build.

## Scripts

Every script prints one JSON object as its last stdout line. Write every path in full.

| Script and arguments | Prints | Exit |
|---|---|---|
| `addons_paths.sh [--series V]` | the trees to grep and the missing ones | 0 |
| `oca_check.py [PATH ...] --series 16.0,17.0,18.0,19.0 [--csv inventory.csv]` | whether each local module is an OCA module, verified against the org | 0 · 2 PARTIAL SCAN |
| `oca_catalog.py --series 19.0 -o catalog.csv` | every OCA module on a series as one CSV (about 1,300 rows) | 0 |

Run `oca_check.py` before `oca_catalog.py`: the catalog reuses the branch listings the
checker caches. Both need an authenticated `gh`.

`exit 2` — PARTIAL SCAN: network trouble, or more than 10% of repos failed → a partial
scan proves presence, never absence; never report `none` from it. Run it again, or stop.

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

Client-facing output is normal prose. These verdicts are proposals for a human review.

## References

| File | Read when |
|---|---|
| [method.md](./references/method.md) | Before any native or OCA review |

Grep roots and patterns: `odoo-devcontainer/references/searching.md`.

## Stop conditions

- `missing` trees from `addons_paths.sh` → stop, name the tree and the path.
- `oca_check.py` exit 2 with no re-run possible → stop, report PARTIAL SCAN.
- A verdict with no cited evidence is not a verdict. Grep again or say unverified.

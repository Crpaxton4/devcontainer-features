# odoo-bin upgrade_code

Mechanical source-rewrite tool inside odoo/odoo since 18.0. Scripts live in
`odoo/upgrade_code/` on each release branch, named `{version}-{nn}-{name}.py`; each exposes
single `upgrade(file_manager)` function. `file_manager` iterates candidate files
(extensions `.py .js .css .scss .xml .csv .po .pot`, `__pycache__` skipped) under every addons
path; scripts mutate `file.content` in memory, CLI writes back only dirty files.
Interim version prefixes (`17.5`, `18.1`..`18.5`, ...) = master-branch snapshots between
majors: `--from 18.0 --to 19.0` run selects every script with `18.0 <= version <= 19.0`.

## Invocation
Flags verified against `odoo/cli/upgrade_code.py` on 19.0 branch:

```bash
# all scripts for one major hop (run the TARGET version's odoo-bin):
odoo-bin upgrade_code --from 18.0 --to 19.0 --addons-path /path/to/addons
# one script only (fuzzy name match):
odoo-bin upgrade_code --script 18.1-00-sql-constraint --addons-path /path/to/addons
# preview:
odoo-bin upgrade_code --from 18.0 --to 19.0 --dry-run --addons-path /path/to/addons
```

| Flag | Meaning |
|---|---|
| `--from VERSION` | run all scripts from this version, inclusive. Mutually exclusive with `--script`; exactly one of two required |
| `--to VERSION` | run scripts up to this version, inclusive; default = running odoo-bin's own release version |
| `--script NAME` | run single script; matched as glob `*NAME*.py` (trailing `.py` optional, first match wins) |
| `--glob PATTERN` | which files considered under each addons path (default `**/*`) |
| `--dry-run` | list files that would be rewritten, write nothing |
| `--addons-path PATH,...` | comma-separated addons directories |

- Exit code `1` = "at least one file was (or would be) rewritten", `0` = no changes.
  Diff signal, not error — no blind CI fail on it.
- Rewritten file paths printed to stdout.
- Also runs standalone without configured Odoo:
  `python3 odoo/cli/upgrade_code.py --from 18.0 --to 19.0 --addons-path /path/to/addons`
  (`--addons-path` required in standalone mode).

## Can / Cannot
Can:
- Mechanical, regex/AST-based text rewrites of `.py`, `.xml`, `.js`, `.css/.scss`, `.csv`, `.po/.pot`.
- Repetitive renames across whole addons tree in one pass (tag renames, decorator/property
  renames, `_sql_constraints` conversion, route type renames, domain literal rewrites).

Cannot:
- Semantic logic changes (API behavior shifts, changed return types, new required arguments).
- `attrs`/`states` -> expression attributes for 16.0 -> 17.0 (predates tool; no script exists).
- Owl/JS component refactors or template restructuring.
- Database or data migration — only edits source files.
- Fixing third-party/external API breakage.
- Scripts best-effort by design (see CLI docstring): expect false positives and misses.

## Rules specific to this tool
- Run TARGET version's odoo-bin — its branch carries accumulated scripts (19.0
  branch still ships 17.5/18.x scripts, so one run covers multi-hop ports).
- Point `--addons-path` at custom addons only — never let it rewrite core or OCA checkouts
  you don't maintain.
- Regex rewrites hit comments, strings, docstrings too — hence hunk-by-hunk review
  required by porting checklist.
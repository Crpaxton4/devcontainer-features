---
name: odoo-upgrade
description: "Plan and run code upgrades of custom Odoo modules between major versions (16.0→17.0→18.0→19.0). Use to migrate or port a module or addon, for breaking changes, a module inventory, or an upgrade estimate or plan."
user-invocable: false
---
# Odoo Code Upgrade Guide

Code porting between major series. The database upgrade is human-run; never attempt it.

Inputs: the source series is `ODOO_VERSION` of this shell; the target series comes from
the request. `preflight.sh --target-series <target>` checks the target series.

Load references as needed, never all at once.

## Index

| Topic             | Load When                                                    | Ref                                                                    |
| ----------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Upgrade project SOP | Anything past the code port: planning, staging DB upgrade, cutover, tickets, functional requirements | the `odoo-dev:odoo-upgrade-project` skill |
| Module Inventory  | Start of an upgrade project; estimate or plan effort; build the inventory workbook | [inventory.md](./references/inventory.md)                               |
| Porting Checklist | Port any module: workflow order and the verification loop   | [porting-checklist.md](./references/porting-checklist.md)               |
| Migration Scripts | A ported module renamed its own field, model or xmlid        | [migrations.md](./references/migrations.md)                             |
| upgrade_code Tool | Run or explain odoo-bin upgrade_code (target 18.0 or later)  | [upgrade-code-tool.md](./references/upgrade-code-tool.md)               |
| 17.0 Changes      | Port code to 17.0 (from 16.0)                                | [17.0/changes.md](./references/17.0/changes.md)                         |
| 18.0 Changes      | Port code to 18.0 (from 17.0)                                | [18.0/changes.md](./references/18.0/changes.md)                         |
| upgrade_code Scripts | What each upgrade_code script rewrites, by the series that ships it | [upgrade-code-scripts.md](./references/upgrade-code-scripts.md) |
| 19.0 Changes      | Port code to 19.0 (from 18.0)                                | [19.0/changes.md](./references/19.0/changes.md)                         |

A new Odoo version adds a `references/XX.0/` folder and rows here. Folder `XX.0/` holds
the changes that arrive in XX.0.

Friction goes into `task.json` through `upgrade-state.sh friction add` (the four fields
`tool`, `expected`, `observed`, `did`, plus `class`). The orchestrator reads `friction[]` after the run.

## Scripts

Write every path in full: `<base directory>/scripts/<script>`.

| Script and arguments | Prints | Exit |
| --- | --- | --- |
| `upgrade-preflight.sh --target V --project "<name>" [--ssh H] [--repo-root DIR]` | one `PASS`/`FAIL` line per check on stderr, then one JSON line: `ok`, `failures[]`, `source_db`, `enterprise_path`, `repo`, `repo_root`, `base_branch`, `env_fingerprint` | 0 ok · 10 a check failed · 2 usage |
| `inventory.sh --artifacts DIR --worktree W --series S [--ssh H]` | runs `module_inventory.py`, `oca_check.py` and, with `--ssh`, `studio_inventory.py`, with absolute outputs under `DIR/inventory/` (`inventory.csv`, `oca_index.json`, `studio.csv`); one `PASS`/`FAIL` line per step on stderr, then one JSON line: `ok`, `inventory_csv`, `oca_index`, `studio_csv` (null without `--ssh`), `failures[]` | 0 ok · 10 a step failed · 2 usage |
| `port-plan.py --from S --to T --worktree W --artifacts A` | `A/plan.json` (`from`, `to`, `hops[]`, `order[]`, `items[]` with `module`, `depends[]`, `hits[]`) from the manifests under W and `references/detection-patterns.tsv`; one summary line on stdout | 0 · 2 usage or dependency cycle |
| `port-module.sh <module> --to T --worktree W [--from S]` | runs `odoo-bin upgrade_code` on W, restores every tracked path outside `W/<module>`, then runs `odoo-module-migrate --no-commit`; commits nothing; one JSON line: `module`, `from`, `to`, `upgrade_code` (`rc`, `files_changed[]`), `migrator` (`ran`, `rc`, `files_changed[]`), `ok` | 0 ok · 1 a tool failed · 2 usage or `odoo-module-migrate` missing (`pip install 'odoo-module-migrator==0.5.0'`) |
| `xpath-check.py --worktree W --series T --source DIR[,DIR…] [--modules a,b]` | applies every custom view `inherit_id` and Owl `t-inherit` (extension mode, a static approximation) against the target arches with `odoo.tools.template_inheritance`; one JSON line: `series`, `checked`, `missing`, `rows[]` (`view`, `module`, `xpath`, `status` ok/missing, `error`). Runs before the first install run | 0 all ok · 1 any miss · 2 usage or Odoo not importable (run in the target-series devcontainer) |
| `smoke_module.py --worktree W --series T --out DIR [--modules a,b]` | generates the throwaway module `upgrade_smoke` under `DIR/upgrade_smoke/`: an `HttpCase` loading the web client, `/shop` and each custom window action (a console error fails), and a report render per inherited report template; prints the module path, then one JSON line: `module_dir`, `http_tests`, `report_tests` | 0 · 2 usage |
| `upgrade_tests.sh --repo R --task ID --worktree W --series T --artifacts A --session-id S [--enterprise DIR]` | generates the smoke module into `A/tests/`, runs the tests of each ported module with a `tests/` dir, of `upgrade_smoke` and of the direct core dependencies (`--test-tags /<dep>`) through `session-db.sh run`; writes `A/tests/summary.json` (`modules`, `total`, `passed`; `total.run` 0 is failed) and marks `tests` and `tested` in the upgrade state | 0 passed · 1 failed · 2 usage |
| `port-lint.sh <worktree> --series T [--modules a,b]` | runs `pylint --load-plugins=pylint_odoo` and `oca-checks-odoo-module` on the module directories; one JSON line: `series`, `modules[]`, `errors[]`, `warnings[]` (entries `tool`, `module`, `path`, `line`, `code`, `message`), `ok`. Runs after `port-module.sh` and before the install loop | 0 no errors · 1 errors · 2 usage or a tool missing (`pip install pylint-odoo`, `pip install oca-odoo-pre-commit-hooks`) |
| `upgrade-state.sh <artifacts> init\|next\|mark\|drop\|gate\|friction\|answer\|build ...` | the one writer of the `upgrade` section of `task.json`, through `artifact.sh`. `init --task ID --from S --to T --worktree W --branch B --base-branch BB [--ssh H] --plan plan.json --preflight pf.json [--max-minutes N] [--max-install-runs N]`; `next` prints one of `PREFLIGHT`, `PORT <module>`, `GATE workbook`, `INSTALL`, `TEST`, `PR`, `STOP done\|blocked_env\|killed\|budget\|failed` (`budget`: a limit from `init` is passed; `killed`: the kill file exists, and the unattended guard then denies every tool but this script); `mark <module> status <state>` (forward only: inventoried, verdict, ported, dropped, installed, tested; `ported` checks `port.commit` and the manifest version) or `mark <module> verdict\|port\|tests --json O`; `drop <module> --config-step T --prod-uninstall --assumption ID [--question T] [--reason T] [--alternative T]` (records the drop and the enrich note, then `git rm`s and commits the module; exit 4 and a restore on failure); `gate workbook --problems N --path P`; `gate install --summary F`; `gate update --summary F --check G` (records `update {runs, last {summary, green, check}}`; `init --upgraded-dump DB_OR_PATH` stores `inputs.upgraded_dump`, and with it `next` prints `UPDATE` after `TEST` until the update is green); `friction add --tool T --expected E --observed O --did D --class tooling\|porting_fact [--series-from S --series-to T2 --symptom-grep G --fix F]` (`observed` quotes the tool output verbatim, empty exits 2; a `porting_fact` needs all four bracketed flags) and `friction list` (the array as JSON); `answer <id> <text> [--by NAME]` (sets `answer`, `answered_by` and `answered_at` on the `assumptions[]` entry; unknown id exits 4) and `answer --list` (the open entries as JSON); `build` derives the `build` section | 0 · 2 usage · 4 invalid move or check · 5 no `upgrade` section |
| `update_dump.sh --db NAME --worktree W --series T --artifacts A --session-id S --modules a,b [--enterprise DIR] [--summary F]` | the UPDATE stage on a restored, neutralized, upgraded dump (never created, dropped or stamped): refuses unless `database.is_neutralized` is `true`, then runs `-u <modules>` and `-u all` with `--stop-after-init`, each through the install warning gate (`accepted_warnings.default.txt` plus `A/accepted_warnings.txt`); JSON summary `db`, `modules`, `runs[]` (`step` ported or all, `rc`, `log`, `decisive`, `class` env or port), `warnings_new[]`, `status`, `passed`; logs under `A/update/` | 0 green · 1 a port defect or new warning · 2 usage · 10 not neutralized or environment |
| `post-update-check.py --db NAME --worktree W --base REF --out FILE [--dry-run]` | reads `git diff REF...HEAD` (best-effort regex over the hunks): `recompute[]` (`model`, `field`, `null_rows`) for stored computed fields whose `@api.depends` changed, `removed_names[]` for fields and models the diff removed, and `hits[]` (`table`, `id`, `column`, `name`) where those names still occur in `ir_cron`, `ir_act_server`, `base_automation`, `ir_filters` and `ir_rule` code and domains; `--dry-run` prints the first two without a database; `psycopg2` when importable, else `psql` | 0 clean · 1 any hit or NULL row · 2 usage or connection |
| `module_inventory.py [PATH ...] [-o FILE]` | writes the inventory CSV to `-o` (default `./inventory.csv`; default tree `/mnt/extra-addons`) | 0 · 1 no module found · 2 usage |
| `studio_inventory.py [--db NAME (optional with --ssh)] [--ssh user@host] [--artifacts DIR] [--csv studio.csv]` | the Studio and UI-built artifacts, read-only SELECTs, through `psql` on the host with `--ssh`; outputs under `DIR/inventory/` | 0 · 2 usage or no connection |
| `build_workbook.py --workdir DIR -o out.xlsx` | the 4-sheet workbook from the seed CSV and the `enrich_<module>.json` records; the last line carries `problems: N` | 0 · 1 problems found |
| `install_all.sh --db NAME --session-id ID --artifacts D --addons-path D [--enterprise D] [--data-dir D] [--conf F\|--no-conf] [--template] [--per-tree] [--summary F] [--expect modules.json] [--previous last.json] [--force]` | the JSON summary with `status`, `progress` (with `--previous`: true when a failing tree now passes or fails on another module or line, false on the same module and line, null when the last run had no failure; also printed on stderr; a non-JSON file is exit 2), `series`, `trees[]` (each with `class` env or port), `full`, `env_failure`, `skipped[]` (`{module, reason}`: `installable: False`, a manifest version not in the target series, or a dependency on a skipped module; each also prints `SKIP <module>: <reason>` on stderr), `expected_but_skipped[]` (modules named by `--expect` that were skipped; the run then exits 1), `passed`, `session_id`, `template.dropped[]` (stale `odoo_tpl_*` databases it dropped), `warnings_new[]` and `warnings_accepted` (the warning gate: an `odoo.*` WARNING line after module loading starts that neither `scripts/accepted_warnings.default.txt` nor the optional `<artifacts>/accepted_warnings.txt` accepts makes the run `failed`, exit 1; one line or regex per entry), written on every exit; installs every module on a throwaway DB. `--artifacts` (use `<ARTIFACTS>/install`) and `--session-id` (or `$SESSION_DB_ID`) are required; a target stamped by another session is refused unless `--force`. The test suite passes `--no-conf`; a real run auto-loads `$ODOO_RC`, `/etc/odoo/odoo.conf` or `~/.odoorc` | 0 green · 1 a port defect · 2 usage or safety · 10 environment |

## Rules

- Minimum change: the smallest diff that satisfies the target series. Never refactor,
  restyle or change behaviour while porting.
- Never run a database upgrade, a merge to production, or a change to production data.
- Order: inventory, then the upgrade pass over all modules (upgrade_code, detection
  greps, manual fixes per the target `changes.md`), then `install_all.sh` until green.
- A multi-version jump works every transition reference in sequence. upgrade_code can run
  once across hops with the target odoo-bin; the install loop runs on the final target only.
- upgrade_code only when the target is 18.0 or later.
- Bump each ported module's manifest version prefix to the target series.
- Every verdict in the inventory cites a grepped path, a catalog row or a fetched README.
- Identify the database before you run anything: `psql -Atc "select current_database()"`.
- Studio is not in git: run `studio_inventory.py` beside the code inventory.
- A test restore you performed is not neutralized: `odoo-bin neutralize -d <db>` first. `update_dump.sh` enforces this: it exits 10 unless `database.is_neutralized` is `true`.

---
name: odoo-test-run
description: "Run an Odoo module's unit tests and HttpCase browser tours on a throwaway database and return machine-checkable evidence: tests executed, tours executed, failure lines. Use before a PR, after a fix, or when a green run never said how many tests ran."
user-invocable: false
---
# Odoo Test Run

Run a module's tests on a disposable database and report the result in a shape a
machine can read. `tests_run: 0` is never green. A module that declares tours and ran
none did not pass.

Inputs: the repo name, the task id, the module name, the worktree path, the module's
target series.

## Run the tests

```bash
<base directory>/scripts/run-tests.sh <repo> <task_id> <module> \
  [--test-tags T|all] [--db-suffix S] [--with-tours] [--repo-root DIR] \
  [--addons-path P] [--data-dir DIR] [--target-series NN.0] \
  [--odoo-bin PATH] [--odoo-conf PATH] [--put <artifacts_dir>]
```

→ ```json
{"passed":true,"status":"passed","error":null,"db":"...","module":"...",
 "odoo_version":"18.0","odoo_bin":"odoo","module_target_series":"18.0","mode":"container",
 "produced_by":"run-tests.sh","tests_run":12,
 "suites":[{"module":"my_module","collected":12,"executed":12,"failed":0}],
 "collected_is_executed":true,
 "addons_path":"/mnt/extra-addons/.worktrees/task-30412,/usr/lib/python3/dist-packages/odoo/addons",
 "data_dir":"/tmp/odoo-test-run/data/qoc_test_30412",
 "tours_declared":1,"tours_run":1,"tours_passed":1,
 "failures":[{"test":"...","error":"AssertionError: 2 != 1"}],
 "log_file":"/tmp/...","log_excerpt":"...","db_dropped":true}
```

- The database is `<project>_test_<id><suffix>`, created fresh and dropped on every exit path.
- Exit 0 means the run completed. Read `status`, never `$?`. A test failure is a result.
- `--target-series` is the series the module is for. When it disagrees with the core this
  container runs, the script uses the container's side core, `$ODOO_SRC_ROOT/odooNN-bin`
  with `odooNN.conf` beside it (`ODOO_SRC_ROOT` defaults to `/var/lib/odoo/src`), if both
  exist, and the result reports that core in `odoo_version` and `odoo_bin`. Without one it
  gives `status: "series_mismatch"`, `passed: false`, `tests_run: 0` and an `error` naming
  both series and the side-core paths it looked for. Nothing ran. Pass the flag whenever
  the target is known.
- `--odoo-bin PATH` runs that executable instead of `odoo` and `--odoo-conf PATH` passes
  `-c PATH` to it. `--odoo-bin` forces `container` mode (exit 2 when it is not runnable
  here) and turns the auto-select off. The core-addons probe imports the `odoo` package
  found beside the binary (`<dir>/odooNN`, then `<dir>`).
- `--test-tags` takes Odoo's grammar: `/module`, `/module:Class`, `/module:Class.method`,
  `-tag` to exclude, comma-separated. The default is `/<module>`, so only the module under
  test runs. `--test-tags all` removes the filter and runs the tests of every installed
  module, which does not finish in a usable time on a module with many dependencies.
- The addons path is stated, never read from `odoo.conf`: the worktree first, then the
  core addons of the running Odoo. The data dir is stated too, per run. `--addons-path`
  and `--data-dir` replace them.
- Enterprise is opt-in: set `ODOO_ENTERPRISE_ADDONS` to its directory when the module needs it.
- Two modes are detected, `container` first: `container` when `odoo` and `pg_isready`
  answer here (inside the stack), else `host` (docker and a running
  `<project>-odoo-1`). Exit 2 means neither is available.
- `--with-tours` resolves a browser first and exits 6 instead of running when there is none.
- `--put <artifacts_dir>` writes the result into `<artifacts_dir>/task.json` under
  `test.<module>` through the plugin's artifact script. stdout still prints the JSON.
  Exit 7 means the write failed and the reason is on stderr.

## Browser for tours

```bash
<base directory>/scripts/browser-ensure.sh [--install | --check]
```

→ `{"browser_bin","source","version","websocket_client","screenshots_dir","screencasts_dir"}`

Search order: `$ODOO_BROWSER_BIN`, then `google-chrome`, `chromium`, `chromium-browser`,
`google-chrome-stable` on PATH, then the Playwright cache. `--install` fetches one; it is
off by default. `--check` prints one line of prose, installs nothing, exits 0. The script
also checks that `websocket-client` imports and prepares writable screenshot directories.

## Reading the result

| Field | Rule |
|---|---|
| `status` | `passed` \| `failed` \| `registry_aborted` \| `series_mismatch`. The field to branch on |
| `error` | the decisive log line for `registry_aborted`; both series for `series_mismatch`; `null` otherwise |
| `odoo_version`, `odoo_bin`, `module_target_series` | the core this run used; the executable it ran (`odoo` unless a side core was used); the series the module was declared for (`null` without the flag) |
| `tests_run` | must be above 0. Zero is never green |
| `suites[]` | per module: `collected`, `executed`, `failed` |
| `collected_is_executed` | always `true`; Odoo logs no separate collected count |
| `tours_declared` against `tours_run` | declared above 0 and run 0 means the tours did not run; re-run with `--with-tours` |
| `failures[].error` | the extracted exception line, what you hand to whoever fixes it |
| `addons_path`, `data_dir` | what the run stated; check these first when a suite reports nothing |
| `log_excerpt`, `log_file` | internal only |

`registry_aborted` means the registry died before any test was collected. It is not
"this module has no tests". Fix the cause in `error` and run again.

Output contract: the JSON is the evidence, verbatim. Nothing from `log_file` or
`log_excerpt` is published anywhere.

## Environment knobs

| Variable | Default | Why |
|---|---|---|
| `WORKTREE_ADDONS_BASE` | `/mnt/extra-addons` | Where the worktree appears to the Odoo process |
| `WORKTREE_SUBDIR` | `.worktrees` | Must match the worktree layout |
| `TEST_DB_ROLE` | the project name (host mode) | The postgres role that owns test databases |
| `ODOO_SHARED_DB_CONTAINER` | `odoo-shared-db-1` | Host mode only |
| `ODOO_TEST_ARTIFACTS_DIR` | `$TMPDIR/odoo-test-run` | Screenshots, screencasts and the per-run data dir |
| `ODOO_ENTERPRISE_ADDONS` | unset | The enterprise tree, opt-in |
| `ODOO_SRC_ROOT` | `/var/lib/odoo/src` | Where the side cores `odooNN-bin` and `odooNN.conf` live |
| `DB_MAXCONN`, `HTTP_PORT_BASE` | `8`, `18000` | Concurrency isolation |

Exit codes: 0 ran (read `status`) · 2 usage or environment · 6 tours requested but no
browser · 7 `--put` could not write.
